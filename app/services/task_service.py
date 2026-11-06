from __future__ import annotations

import hashlib
import json
import logging

from app.analyzers.ticket_analyzer import TicketAnalyzer
from app.analyzers.message_filter import split_messages
from app.integrations.llm import LLMProvider
from app.integrations.telegram import TelegramNotifier
from app.models.ticket import TicketAnalysis, TicketInput
from app.privacy.redactor import Redactor
from app.services.repository import TaskRecord, TaskRepository


logger = logging.getLogger(__name__)


class TaskService:
    def __init__(
        self,
        repository: TaskRepository,
        analyzer: TicketAnalyzer,
        redactor: Redactor,
        llm: LLMProvider,
        telegram: TelegramNotifier,
    ) -> None:
        self.repository = repository
        self.analyzer = analyzer
        self.redactor = redactor
        self.llm = llm
        self.telegram = telegram

    @staticmethod
    def version_hash(ticket: TicketInput) -> str:
        content = ticket.model_dump(mode="json", exclude={"updated_at"})
        canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def start(self, ticket: TicketInput) -> tuple[TaskRecord, bool]:
        return self.repository.create_or_get(
            ticket.ticket_id,
            self.version_hash(ticket),
            ticket.model_dump(mode="json"),
        )

    def safe_llm_context(self, ticket: TicketInput) -> dict:
        """Send only meaningful comments; routing templates remain internal noise."""
        meaningful, _service = split_messages(ticket.messages)
        payload = ticket.model_dump(mode="json")
        payload["messages"] = [message.model_dump(mode="json") for message in meaningful]
        return self.redactor.for_external_llm(payload)

    async def process(self, task_id: str, *, notify: bool = True) -> TicketAnalysis | None:
        record = self.repository.get(task_id)
        if record is None:
            return None
        if record.status == "completed":
            return self.result(task_id)
        if record.status != "queued":
            return None
        self.repository.set_processing(task_id)
        try:
            ticket = TicketInput.model_validate_json(record.input_json)
            draft = self.analyzer.analyze(ticket)
            safe_context = self.safe_llm_context(ticket)
            result = await self.llm.refine(safe_context, draft)
            result.report_markdown = self.analyzer.render_markdown(ticket.ticket_number, result)
            self.repository.set_completed(task_id, result.model_dump(mode="json"))
            if notify:
                try:
                    await self.telegram.send(result.report_markdown)
                except Exception:
                    logger.exception("Telegram notification failed for task %s", task_id)
            return result
        except Exception as exc:
            logger.exception("Task %s failed", task_id)
            self.repository.set_error(task_id, str(exc))
            return None

    def get(self, task_id: str) -> TaskRecord | None:
        return self.repository.get(task_id)

    def result(self, task_id: str) -> TicketAnalysis | None:
        record = self.repository.get(task_id)
        if not record or not record.result_json:
            return None
        return TicketAnalysis.model_validate_json(record.result_json)
