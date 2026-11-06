from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re

from app.integrations.metabase_tickets import MetabaseTicketSource
from app.analyzers.message_filter import split_messages
from app.models.ticket import TicketAnalysis, TicketInput
from app.privacy.redactor import Redactor
from app.services.monitor_repository import MonitorRepository
from app.services.task_service import TaskService


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PollResult:
    baseline_created: bool
    current_count: int
    detected_count: int
    delivered_count: int
    failed_count: int
    deferred_count: int


class MetabaseMonitor:
    def __init__(
        self,
        *,
        source: MetabaseTicketSource,
        repository: MonitorRepository,
        task_service: TaskService,
        redactor: Redactor,
        poll_interval_seconds: float,
        empty_ticket_grace_seconds: float,
        baseline_on_first_run: bool,
    ) -> None:
        self.source = source
        self.repository = repository
        self.task_service = task_service
        self.redactor = redactor
        self.poll_interval_seconds = max(5.0, poll_interval_seconds)
        self.empty_ticket_grace_seconds = max(0.0, empty_ticket_grace_seconds)
        self.baseline_on_first_run = baseline_on_first_run
        self._stop = asyncio.Event()

    async def poll_once(self) -> PollResult:
        headers = await asyncio.to_thread(self.source.fetch_current_headers)
        snapshot = self.repository.sync_snapshot(
            self.source.performer,
            set(headers),
            baseline_on_first_run=self.baseline_on_first_run,
        )
        delivered = 0
        failed = 0
        deferred = 0
        for event in self.repository.claim_pending(self.source.performer):
            try:
                if event.task_id:
                    record = self.task_service.get(event.task_id)
                    if record is None or record.status != "completed" or not record.result_json:
                        raise RuntimeError("Stored ticket analysis is unavailable")
                    ticket = TicketInput.model_validate_json(record.input_json)
                    result = TicketAnalysis.model_validate_json(record.result_json)
                else:
                    ticket = await asyncio.to_thread(
                        self.source.fetch_ticket,
                        event.ticket_id,
                        event.generation,
                    )
                    meaningful, _service = split_messages(ticket.messages)
                    event_age = (
                        datetime.now(timezone.utc) - event.detected_at.astimezone(timezone.utc)
                    ).total_seconds()
                    if not meaningful and event_age < self.empty_ticket_grace_seconds:
                        self.repository.mark_retry(
                            event.event_id,
                            "Waiting for CallCenter comments to become available",
                        )
                        deferred += 1
                        continue
                    if not meaningful:
                        if not event.content_notice_sent:
                            await self.task_service.telegram.send(
                                format_waiting_for_content_notification(
                                    ticket,
                                    assignment_detected_at=event.detected_at,
                                )
                            )
                        self.repository.mark_waiting_for_content(event.event_id)
                        deferred += 1
                        continue
                    record, created = self.task_service.start(ticket)
                    if created:
                        result = await self.task_service.process(record.task_id, notify=False)
                    else:
                        result = self.task_service.result(record.task_id)
                        if result is None and record.status == "queued":
                            result = await self.task_service.process(record.task_id, notify=False)
                    if result is not None:
                        self.repository.mark_analyzed(event.event_id, record.task_id)
                if result is None:
                    raise RuntimeError("Ticket analysis did not complete")
                await self.task_service.telegram.send(
                    format_assignment_notification(
                        ticket,
                        result,
                        self.redactor,
                        assignment_detected_at=event.detected_at,
                        followup=event.content_notice_sent,
                    )
                )
                self.repository.mark_delivered(event.event_id, record.task_id)
                delivered += 1
            except Exception as exc:
                failed += 1
                self.repository.mark_retry(event.event_id, f"{type(exc).__name__}: {exc}")
                logger.exception(
                    "Metabase assignment processing failed for ticket %s (attempt %s)",
                    event.ticket_id,
                    event.attempts,
                )
        return PollResult(
            baseline_created=snapshot.baseline_created,
            current_count=snapshot.current_count,
            detected_count=snapshot.detected_count,
            delivered_count=delivered,
            failed_count=failed,
            deferred_count=deferred,
        )

    async def run_forever(self) -> None:
        logger.info(
            "Metabase monitor started for performer %s (interval %.1fs)",
            self.source.performer,
            self.poll_interval_seconds,
        )
        while not self._stop.is_set():
            try:
                result = await self.poll_once()
                if result.baseline_created:
                    logger.info(
                        "Metabase baseline saved with %s current tickets; no historical notifications sent",
                        result.current_count,
                    )
                elif result.detected_count or result.failed_count or result.deferred_count:
                    logger.info(
                        "Metabase poll: current=%s detected=%s delivered=%s failed=%s deferred=%s",
                        result.current_count,
                        result.detected_count,
                        result.delivered_count,
                        result.failed_count,
                        result.deferred_count,
                    )
            except Exception:
                logger.exception("Metabase polling cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                pass
        logger.info("Metabase monitor stopped")

    def stop(self) -> None:
        self._stop.set()


def format_assignment_notification(
    ticket: TicketInput,
    result: TicketAnalysis,
    redactor: Redactor,
    *,
    now: datetime | None = None,
    assignment_detected_at: datetime | None = None,
    followup: bool = False,
) -> str:
    def clip(value: str, limit: int) -> str:
        compact = " ".join(value.split())
        return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"

    local_now = now or datetime.now(ticket.created_at.tzinfo)
    detected_local = (assignment_detected_at or local_now).astimezone(ticket.created_at.tzinfo)
    age_hours = max(0, int((local_now - ticket.created_at).total_seconds() // 3600))
    raw_messages = [
        message for message in ticket.messages if not str(message.id).startswith("assignment-")
    ]
    meaningful_messages, service_messages = split_messages(raw_messages)
    # In chat tickets the subscriber's own messages usually contain the most
    # compact problem statement. Operator greetings and PII collection should
    # not push the actual symptom beyond Telegram's short preview.
    subscriber_messages = [
        message for message in meaningful_messages if message.author_role.casefold() == "subscriber"
    ]
    description_messages = subscriber_messages or meaningful_messages
    descriptions: list[str] = []
    for message in description_messages:
        safe_text = redactor.redact_text(message.text)
        visible_text = re.sub(r"\[[^\]]+\]", "", safe_text)
        if len(re.sub(r"\W", "", visible_text, flags=re.UNICODE)) < 4:
            continue
        descriptions.append(safe_text)
    description = clip(" / ".join(descriptions), 700) if descriptions else "Комментариев пока нет."
    subscriber = f"*{ticket.subscriber.id_abon}" if ticket.subscriber.id_abon else "не указан"
    confidence = {"low": "низкая", "medium": "средняя", "high": "высокая"}[result.confidence]
    mode = (
        result.analysis_model or "языковая модель"
        if result.analysis_mode == "llm"
        else "локальный резервный анализ"
    )
    hypotheses = list(dict.fromkeys(result.hypotheses))[:2]
    checks = list(dict.fromkeys(result.required_checks))[:4]
    verdict = "\n".join(f"• {clip(item, 260)}" for item in hypotheses)
    checks_text = "\n".join(f"{index}. {clip(item, 220)}" for index, item in enumerate(checks, 1))
    relevant_cases = [
        case for case in result.similar_cases if case.match_status in {"relevant", "partial"}
    ]
    similar = ""
    if relevant_cases:
        case = sorted(
            relevant_cases,
            key=lambda item: (item.match_status == "relevant", item.score),
            reverse=True,
        )[0]
        status = "подходит" if case.match_status == "relevant" else "частично похож"
        similar = (
            "\n\n📚 Похожий случай: "
            f"{clip(case.title, 180)} ({status})\n"
            f"{clip(case.resolution, 300)}"
        )
    reply = clip(result.operator_reply, 550)
    title = (
        "🤖 ГОТОВ РАЗБОР ТИКЕТА ДЛЯ " + ticket.performer
        if followup
        else "🚨 НОВЫЙ ТИКЕТ ДЛЯ " + ticket.performer
    )
    text = (
        title + "\n\n"
        f"🎟 Тикет {ticket.ticket_number}\n"
        f"📱 Абонент: {subscriber}\n"
        f"📅 Создано: {ticket.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📥 Назначение обнаружено: {detected_local.strftime('%d.%m.%Y %H:%M')}\n"
        f"⏱ Возраст: {age_hours}ч\n"
        f"📌 Тема: {ticket.subject}\n\n"
        f"📝 Описание: {description}\n\n"
        f"🤖 ИИ-разбор: {result.category_label or result.category}\n"
        f"Уверенность: {confidence} · {mode}\n\n"
        f"Суть: {clip(result.summary, 450)}\n\n"
        f"Вердикт:\n{verdict or '• Причину определить нельзя по доступным данным.'}\n\n"
        f"Проверить:\n{checks_text or '1. Уточнить описание проблемы.'}"
        f"{similar}\n\n"
        f"💬 Оператору: {reply}\n\n"
        "⚠️ Предварительный результат — требуется проверка сотрудником."
    )
    # Notifications must always fit one Telegram message. The full analysis
    # remains available in SQLite/API for audit, but is not dumped into chat.
    return text if len(text) <= 3800 else text[:3740].rstrip() + "\n\n…полный разбор сохранён в системе."


def format_waiting_for_content_notification(
    ticket: TicketInput,
    *,
    assignment_detected_at: datetime,
) -> str:
    detected_local = assignment_detected_at.astimezone(ticket.created_at.tzinfo)
    subscriber = f"*{ticket.subscriber.id_abon}" if ticket.subscriber.id_abon else "не указан"
    return (
        "🚨 НОВЫЙ ТИКЕТ ДЛЯ " + ticket.performer + "\n\n"
        f"🎟 Тикет {ticket.ticket_number}\n"
        f"📱 Абонент: {subscriber}\n"
        f"📅 Создано: {ticket.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📥 Назначение обнаружено: {detected_local.strftime('%d.%m.%Y %H:%M')}\n"
        f"📌 Тема: {ticket.subject}\n\n"
        "📝 История обращения ещё не появилась в доступной выгрузке Metabase. "
        "Это не означает, что внутри тикета нет комментариев.\n\n"
        "⏳ ИИ-разбор отложен: сервис продолжит проверять тикет и пришлёт "
        "отдельный короткий разбор, когда комментарии синхронизируются."
    )
