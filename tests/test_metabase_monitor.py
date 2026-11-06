import asyncio
from datetime import datetime, timezone

from app.analyzers.ticket_analyzer import TicketAnalyzer
from app.knowledge.store import KnowledgeStore
from app.models.ticket import Subscriber, TicketInput, TicketMessage
from app.privacy.redactor import Redactor
from app.services.metabase_monitor import MetabaseMonitor, format_assignment_notification
from app.services.metabase_monitor import format_waiting_for_content_notification
from app.services.monitor_repository import MonitorRepository


def test_notification_contains_ticket_history_and_redacts_phone(tmp_path) -> None:
    ticket = TicketInput(
        ticket_id=68548052,
        ticket_number="68-548-052",
        subject_id=14819,
        subject="Проблемы МП Goodline Город/ЛК",
        group_id=142,
        performer="@Valeri_Nikulina",
        status=0,
        created_at=datetime(2026, 9, 7, 15, 19, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 7, 17, 23, tzinfo=timezone.utc),
        subscriber=Subscriber(id_abon=2465806),
        messages=[
            TicketMessage(
                id="m1",
                created_at=datetime(2026, 9, 7, 15, 20, tzinfo=timezone.utc),
                author_role="operator",
                scope="private",
                text="Абонент звонил с 8 (999) 430-30-15: услуга не отображается",
                author="OPERATOR_EV",
            )
        ],
    )
    analysis = TicketAnalyzer(KnowledgeStore(tmp_path)).analyze(ticket)

    text = format_assignment_notification(
        ticket,
        analysis,
        Redactor(),
        now=datetime(2026, 9, 7, 18, 19, tzinfo=timezone.utc),
    )

    assert "Тикет 68-548-052" in text
    assert "@Valeri_Nikulina" in text
    assert "*2465806" in text
    assert "Назначение обнаружено: 07.09.2026 18:19" in text
    assert "📝 Описание:" in text
    assert "[ТЕЛЕФОН]" in text
    assert "999" not in text
    assert "🤖 ИИ-разбор:" in text
    assert len(text) <= 3800
    assert "##" not in text


def test_empty_ticket_gets_short_waiting_notice_without_fake_analysis() -> None:
    ticket = TicketInput(
        ticket_id=68589089,
        ticket_number="68-589-089",
        subject_id=14819,
        subject="Проблемы МП Goodline Город/ЛК",
        group_id=142,
        performer="@Valeri_Nikulina",
        status=0,
        created_at=datetime(2026, 9, 9, 14, 48, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 9, 14, 52, tzinfo=timezone.utc),
        subscriber=Subscriber(id_abon=1977950),
        messages=[],
    )

    text = format_waiting_for_content_notification(
        ticket,
        assignment_detected_at=datetime(2026, 9, 9, 14, 53, tzinfo=timezone.utc),
    )

    assert "история обращения ещё не появилась" in text.casefold()
    assert "ИИ-разбор отложен" in text
    assert "Вердикт" not in text
    assert len(text) < 1000


def test_empty_ticket_notice_is_sent_once_and_analysis_is_not_started(tmp_path) -> None:
    ticket = TicketInput(
        ticket_id=68589089,
        ticket_number="68-589-089",
        subject_id=14819,
        subject="Проблемы МП Goodline Город/ЛК",
        group_id=142,
        performer="@Valeri_Nikulina",
        status=0,
        created_at=datetime(2026, 9, 9, 14, 48, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 9, 14, 52, tzinfo=timezone.utc),
        subscriber=Subscriber(id_abon=1977950),
        messages=[],
    )

    class Source:
        performer = "NIKULINA_VS"

        def fetch_current_headers(self):
            return {ticket.ticket_id: object()}

        def fetch_ticket(self, ticket_id, generation):
            return ticket

    class Telegram:
        def __init__(self):
            self.messages = []

        async def send(self, text):
            self.messages.append(text)

    class Tasks:
        def __init__(self):
            self.telegram = Telegram()
            self.started = 0

        def start(self, value):
            self.started += 1
            raise AssertionError("Empty ticket must not start AI analysis")

    tasks = Tasks()
    monitor = MetabaseMonitor(
        source=Source(),
        repository=MonitorRepository(tmp_path / "state.db"),
        task_service=tasks,
        redactor=Redactor(),
        poll_interval_seconds=60,
        empty_ticket_grace_seconds=0,
        baseline_on_first_run=False,
    )

    first = asyncio.run(monitor.poll_once())
    second = asyncio.run(monitor.poll_once())

    assert first.deferred_count == 1
    assert second.deferred_count == 1
    assert len(tasks.telegram.messages) == 1
    assert tasks.started == 0
