from datetime import datetime, timezone

from app.analyzers.message_filter import split_messages
from app.privacy.redactor import Redactor
from app.services.task_service import TaskService
from app.models.ticket import TicketMessage


def test_separates_service_events() -> None:
    messages = [
        TicketMessage(id=1, created_at=datetime.now(timezone.utc), author_role="system", scope="private", text="Назначен исполнитель"),
        TicketMessage(id=2, created_at=datetime.now(timezone.utc), author_role="operator", scope="private", text="Не проходит оплата"),
    ]
    meaningful, service = split_messages(messages)
    assert [item.id for item in meaningful] == [2]
    assert [item.id for item in service] == [1]


def test_routing_template_and_callback_notes_do_not_affect_problem_text() -> None:
    now = datetime.now(timezone.utc)
    messages = [
        TicketMessage(
            id=1,
            created_at=now,
            author_role="operator",
            scope="private",
            text="Баг в приложении. На скриншоте в красном блоке",
        ),
        TicketMessage(
            id=2,
            created_at=now,
            author_role="operator",
            scope="private",
            text="Скрипт обработки тикетов\n1. Техническая проблема\n2. Проблема с домофонией",
        ),
        TicketMessage(
            id=3,
            created_at=now,
            author_role="operator",
            scope="private",
            text="не успела все отзвонить - завтра выходной",
        ),
        TicketMessage(
            id=4,
            created_at=now,
            author_role="operator",
            scope="private",
            text="https://bugshaker.goodline.info/storage/example/screenshot.png",
        ),
    ]

    meaningful, service = split_messages(messages)

    assert [item.id for item in meaningful] == [1]
    assert [item.id for item in service] == [2, 3, 4]


def test_llm_context_excludes_routing_template(ticket_payload: dict) -> None:
    ticket_payload["messages"].append(
        {
            "id": 2,
            "created_at": "2026-09-07T15:21:00+07:00",
            "author_role": "operator",
            "scope": "private",
            "text": "Скрипт обработки тикетов: проблема с домофонией",
        }
    )
    from app.models.ticket import TicketInput

    service = object.__new__(TaskService)
    service.redactor = Redactor()

    context = service.safe_llm_context(TicketInput.model_validate(ticket_payload))

    assert len(context["messages"]) == 1
    assert "домофони" not in context["messages"][0]["text"].casefold()
