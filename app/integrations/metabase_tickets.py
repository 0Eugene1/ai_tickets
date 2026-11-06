from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable

from app.config import Settings
from app.integrations.metabase import MetabaseClient, MetabaseConfig, MetabaseError
from app.models.ticket import DiagnosticCheck, Diagnostics, Subscriber, TicketInput, TicketMessage


_SAFE_LOGIN = re.compile(r"^[A-Za-z0-9_]{1,64}$")


@dataclass(frozen=True)
class MetabaseTicketHeader:
    ticket_id: int
    subscriber_id: int
    subject_id: int
    subject: str
    group_id: int
    status: int
    created_at: datetime
    updated_at: datetime

    @property
    def ticket_number(self) -> str:
        raw = str(self.ticket_id)
        if len(raw) > 6:
            return f"{raw[:-6]}-{raw[-6:-3]}-{raw[-3:]}"
        return raw


class MetabaseTicketSource:
    """Read-only adapter from the Metabase CallCenter tables to TicketInput."""

    _HEADER_SELECT = """
        SELECT
            P.ID_PROBLEM,
            P.ID_ABON,
            P.ID_SUBJECT,
            S.SUBJECT AS SUBJECT_NAME,
            P.DATE_CREATE,
            P.DATE_LAST,
            P.STATUS,
            P.ID_GROUP
        FROM METRICS.CALLCENTER_PROBLEMS P
        LEFT JOIN METRICS.SUBJECTS S ON S.ID_SUBJECT = P.ID_SUBJECT
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], MetabaseClient] | None = None,
    ) -> None:
        performer = settings.metabase_performer_filter.strip()
        if not _SAFE_LOGIN.fullmatch(performer):
            raise ValueError("METABASE_PERFORMER_FILTER must contain only letters, digits, and underscores")
        self.performer = performer
        self.performer_label = settings.metabase_performer_label.strip() or performer
        self.database_id = settings.metabase_database_id
        self.local_timezone = timezone(timedelta(hours=settings.metabase_utc_offset_hours))
        if client_factory is None:
            config = MetabaseConfig(
                base_url=settings.metabase_base_url or "",
                api_token=settings.metabase_api_token or "",
                auth_mode=settings.metabase_auth_mode,
                timeout_seconds=settings.metabase_timeout_seconds,
                allow_insecure_http=settings.metabase_allow_insecure_http,
            )
            client_factory = lambda: MetabaseClient(config)
        self._client_factory = client_factory

    def fetch_current_headers(self) -> dict[int, MetabaseTicketHeader]:
        query = (
            f"{self._HEADER_SELECT} "
            f"WHERE P.PERFORMER = '{self.performer}' "
            "ORDER BY P.DATE_LAST, P.ID_PROBLEM"
        )
        with self._client_factory() as client:
            rows = client.run_native_select(self.database_id, query)
        headers = (self._header(row) for row in rows)
        return {header.ticket_id: header for header in headers}

    def fetch_ticket(self, ticket_id: int, assignment_generation: int) -> TicketInput:
        numeric_ticket_id = int(ticket_id)
        with self._client_factory() as client:
            header_rows = client.run_native_select(
                self.database_id,
                f"{self._HEADER_SELECT} WHERE P.ID_PROBLEM = {numeric_ticket_id}",
            )
            message_rows = client.run_native_select(
                self.database_id,
                "SELECT ID_PROBLEM, ST_DATE, ST_USER, COMM "
                "FROM METRICS.VW_CALLCENTER_MESSAGES "
                f"WHERE ID_PROBLEM = {numeric_ticket_id} "
                "ORDER BY ST_DATE",
            )
            chat_rows = client.run_native_select(
                self.database_id,
                "SELECT ID, ID_PROBLEM, DT_BEGIN, DT_END, TYPE_MESSAGE, "
                "SENDER, FIO_SENDER, TEXT "
                "FROM METRICS.VW_CHAT_MESSAGES "
                f"WHERE ID_PROBLEM = {numeric_ticket_id} "
                "ORDER BY ID",
            )
        if not header_rows:
            raise MetabaseError(f"Ticket {numeric_ticket_id} is no longer available in Metabase")
        header = self._header(header_rows[0])
        messages: list[TicketMessage] = []
        seen_texts: set[str] = set()
        attachment_found = False

        def add_message(
            *,
            message_id: str,
            created_at: datetime,
            author_role: str,
            text: str,
            author: str,
        ) -> None:
            nonlocal attachment_found
            clean_text = text.strip()
            normalized = " ".join(clean_text.casefold().split())
            if not normalized or normalized == "hidden_form" or normalized in seen_texts:
                return
            seen_texts.add(normalized)
            if "bugshaker.goodline.info" in normalized:
                attachment_found = True
            messages.append(
                TicketMessage(
                    id=message_id,
                    created_at=created_at,
                    author_role=author_role,
                    scope="private",
                    text=clean_text,
                    author=author,
                )
            )

        for index, row in enumerate(message_rows, start=1):
            author = str(row.get("ST_USER") or "system").strip()
            add_message(
                message_id=f"metabase-comment-{numeric_ticket_id}-{index}",
                created_at=self._datetime(row.get("ST_DATE"), fallback=header.updated_at),
                author_role=(
                    "system" if author.casefold() in {"system", "система"} else "operator"
                ),
                text=str(row.get("COMM") or ""),
                author=author,
            )
        for index, row in enumerate(chat_rows, start=1):
            message_type = str(row.get("TYPE_MESSAGE") or "").strip().casefold()
            sender = str(row.get("SENDER") or "").strip()
            author = str(row.get("FIO_SENDER") or sender or message_type or "chat").strip()
            if "абонент" in message_type:
                author_role = "subscriber"
            elif sender.casefold() in {"openfire", "bot", "system"}:
                author_role = "bot"
            else:
                author_role = "operator"
            add_message(
                message_id=f"metabase-chat-{row.get('ID') or index}",
                created_at=self._datetime(
                    row.get("DT_BEGIN") or row.get("DT_END"),
                    fallback=header.updated_at,
                ),
                author_role=author_role,
                text=str(row.get("TEXT") or ""),
                author=author,
            )
        messages.sort(key=lambda item: item.created_at)
        # Makes a repeated assignment a new idempotent task even when its comments did not change.
        messages.append(
            TicketMessage(
                id=f"assignment-{assignment_generation}",
                # The generation already identifies the assignment. A stable timestamp
                # keeps retries byte-identical and therefore idempotent.
                created_at=header.updated_at,
                author_role="system",
                scope="service",
                text=f"Назначен исполнитель: {self.performer}",
            )
        )
        return TicketInput(
            ticket_id=header.ticket_id,
            ticket_number=header.ticket_number,
            subject_id=header.subject_id,
            subject=header.subject,
            group_id=header.group_id,
            performer=self.performer_label,
            status=header.status,
            created_at=header.created_at,
            updated_at=header.updated_at,
            subscriber=Subscriber(id_abon=header.subscriber_id),
            messages=messages,
            diagnostics=Diagnostics(
                checks=(
                    [
                        DiagnosticCheck(
                            name="Скриншот или вложение из приложения",
                            status="found",
                            source="metabase.messages.attachment",
                        )
                    ]
                    if attachment_found
                    else []
                )
            ),
        )

    def _header(self, row: dict[str, Any]) -> MetabaseTicketHeader:
        created_at = self._datetime(row.get("DATE_CREATE"))
        return MetabaseTicketHeader(
            ticket_id=int(row["ID_PROBLEM"]),
            subscriber_id=int(row.get("ID_ABON") or 0),
            subject_id=int(row.get("ID_SUBJECT") or 0),
            subject=str(row.get("SUBJECT_NAME") or "Тема не указана"),
            group_id=int(row.get("ID_GROUP") or 0),
            status=int(row.get("STATUS") or 0),
            created_at=created_at,
            updated_at=self._datetime(row.get("DATE_LAST"), fallback=created_at),
        )

    def _datetime(self, value: Any, *, fallback: datetime | None = None) -> datetime:
        if value in (None, ""):
            if fallback is None:
                raise MetabaseError("Metabase ticket has no creation date")
            return fallback
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError as exc:
                raise MetabaseError("Metabase returned an invalid ticket date") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=self.local_timezone)
        return parsed.astimezone(self.local_timezone)
