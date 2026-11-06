from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Callable

from app.config import Settings
from app.integrations.metabase import MetabaseClient, MetabaseConfig, MetabaseError
from app.models.ticket import DiagnosticCheck, TicketInput


logger = logging.getLogger(__name__)


class MetabaseDiagnosticsProvider:
    """Read-only, allow-listed diagnostic facts for the internal manager workflow."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], MetabaseClient] | None = None,
    ) -> None:
        self.enabled = settings.metabase_diagnostics_enabled
        self.database_id = settings.metabase_database_id
        self.local_timezone = timezone(timedelta(hours=settings.metabase_utc_offset_hours))
        self.max_age = timedelta(days=max(1.0, settings.metabase_diagnostics_max_age_days))
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

    def enrich(self, ticket: TicketInput) -> TicketInput:
        if not self.enabled or not ticket.subscriber.id_abon:
            return ticket
        diagnostics = ticket.diagnostics.model_copy(deep=True)
        checks = list(diagnostics.checks)
        try:
            rows = self._sessions(ticket.subscriber.id_abon)
        except MetabaseError as exc:
            logger.warning("Read-only diagnostics unavailable: %s", type(exc).__name__)
            checks.append(
                DiagnosticCheck(
                    name="Read-only диагностика сессий МПЛК",
                    status="unavailable",
                    value="Metabase временно не ответил; основной разбор продолжен без диагностики",
                    source="metabase.ss_stat_lk_sessions",
                    checked_at=datetime.now(timezone.utc),
                )
            )
            diagnostics.checks = checks
            return ticket.model_copy(update={"diagnostics": diagnostics})

        if not rows:
            checks.append(
                DiagnosticCheck(
                    name="Последние сессии МПЛК",
                    status="not_found",
                    value="Для абонента сессии в доступной read-only витрине не найдены",
                    source="metabase.ss_stat_lk_sessions",
                    checked_at=datetime.now(timezone.utc),
                )
            )
            diagnostics.checks = checks
            return ticket.model_copy(update={"diagnostics": diagnostics})

        latest = rows[0]
        observed_value = latest.get("UPDATED_AT") or latest.get("CREATED_AT")
        if not observed_value:
            checks.append(
                DiagnosticCheck(
                    name="Последняя сессия МПЛК",
                    status="unavailable",
                    value="В read-only витрине найдена сессия без даты; версии не используются как актуальный факт",
                    source="metabase.ss_stat_lk_sessions",
                    checked_at=datetime.now(timezone.utc),
                )
            )
            diagnostics.checks = checks
            return ticket.model_copy(update={"diagnostics": diagnostics})
        observed_at = self._datetime(observed_value)
        age = datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)
        recent_count = sum(
            datetime.now(timezone.utc) - self._datetime(row.get("UPDATED_AT") or row.get("CREATED_AT")).astimezone(timezone.utc)
            <= timedelta(days=7)
            for row in rows
            if row.get("UPDATED_AT") or row.get("CREATED_AT")
        )
        if age <= self.max_age:
            diagnostics.platform = diagnostics.platform or self._platform(latest.get("OS_DEVICE"))
            diagnostics.os_version = diagnostics.os_version or self._text(latest.get("OS_VERSION"))
            diagnostics.app_version = diagnostics.app_version or self._app_version(latest.get("DETAILS"))
            summary = f"последняя сессия {observed_at.astimezone(self.local_timezone):%d.%m.%Y %H:%M}"
            if recent_count:
                summary += f"; за 7 дней найдено сессий: {recent_count}"
            checks.append(
                DiagnosticCheck(
                    name="Read-only профиль устройства и сессий МПЛК",
                    status="ok",
                    value=summary,
                    source="metabase.ss_stat_lk_sessions",
                    checked_at=datetime.now(timezone.utc),
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    name="Последняя сессия МПЛК",
                    status="unavailable",
                    value=(
                        f"последняя запись {observed_at.astimezone(self.local_timezone):%d.%m.%Y}; "
                        "версии могли устареть и не используются как актуальный факт"
                    ),
                    source="metabase.ss_stat_lk_sessions",
                    checked_at=datetime.now(timezone.utc),
                )
            )
        diagnostics.checks = checks
        return ticket.model_copy(update={"diagnostics": diagnostics})

    def _sessions(self, subscriber_id: int) -> list[dict[str, Any]]:
        numeric_id = int(subscriber_id)
        query = (
            "SELECT * FROM ("
            "SELECT OS_DEVICE, OS_VERSION, DETAILS, CREATED_AT, UPDATED_AT, CLOSED "
            "FROM METRICS.SS_STAT_LK_SESSIONS "
            f"WHERE ID_ABON = {numeric_id} "
            "ORDER BY COALESCE(UPDATED_AT, CREATED_AT) DESC"
            ") WHERE ROWNUM <= 20"
        )
        with self._client_factory() as client:
            return client.run_native_select(self.database_id, query)

    def _datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            result = value
        else:
            result = datetime.fromisoformat(str(value))
        if result.tzinfo is None:
            result = result.replace(tzinfo=self.local_timezone)
        return result

    @staticmethod
    def _text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _platform(value: Any) -> str | None:
        text = str(value or "").strip()
        lowered = text.casefold()
        if "android" in lowered:
            return "Android"
        if lowered in {"ios", "iphone", "ipad"} or "apple" in lowered:
            return "iOS"
        return text or None

    @staticmethod
    def _app_version(value: Any) -> str | None:
        if isinstance(value, dict):
            payload = value
        else:
            try:
                payload = json.loads(str(value or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        if not isinstance(payload, dict):
            return None
        for key, item in payload.items():
            normalized = str(key).casefold().replace("_", "-")
            if normalized in {"app-version", "application-version", "version"}:
                text = str(item or "").strip()
                return text or None
        return None
