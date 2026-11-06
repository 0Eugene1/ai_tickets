from datetime import timedelta
from pathlib import Path

from app.config import Settings
from app.analyzers.ticket_analyzer import TicketAnalyzer
from app.integrations.metabase_tickets import MetabaseTicketSource
from app.knowledge.store import KnowledgeStore


class FakeMetabaseClient:
    def __init__(self, responses: list[list[dict]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def run_native_select(self, database_id: int, query: str) -> list[dict]:
        assert database_id == 5
        self.queries.append(query)
        return self.responses.pop(0)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "tasks.db",
        telegram_enabled=False,
        metabase_base_url="https://metabase.internal",
        metabase_api_token="secret",
        metabase_performer_filter="NIKULINA_VS",
        metabase_performer_label="@Valeri_Nikulina",
        metabase_polling_enabled=False,
        metabase_utc_offset_hours=7,
    )


def _header() -> dict:
    return {
        "ID_PROBLEM": 68548052,
        "ID_ABON": 2465806,
        "ID_SUBJECT": 14819,
        "SUBJECT_NAME": "Проблемы МП Goodline Город/ЛК",
        "DATE_CREATE": "2026-09-07T15:19:56",
        "DATE_LAST": "2026-09-07T17:23:00",
        "STATUS": 0,
        "ID_GROUP": 142,
    }


def test_reads_only_current_performer_headers(tmp_path: Path) -> None:
    fake = FakeMetabaseClient([[_header()]])
    source = MetabaseTicketSource(_settings(tmp_path), client_factory=lambda: fake)

    headers = source.fetch_current_headers()

    assert list(headers) == [68548052]
    assert headers[68548052].ticket_number == "68-548-052"
    assert "P.PERFORMER = 'NIKULINA_VS'" in fake.queries[0]
    assert headers[68548052].created_at.utcoffset() == timedelta(hours=7)


def test_builds_ticket_with_history_and_assignment_generation(tmp_path: Path) -> None:
    fake = FakeMetabaseClient(
        [
            [_header()],
            [
                {
                    "ID_PROBLEM": 68548052,
                    "ST_DATE": "2026-09-07T15:20:00",
                    "ST_USER": "OPERATOR_EV",
                    "COMM": "Услуга подключена, но не отображается в приложении",
                }
            ],
            [],
        ]
    )
    source = MetabaseTicketSource(_settings(tmp_path), client_factory=lambda: fake)

    ticket = source.fetch_ticket(68548052, assignment_generation=2)

    assert ticket.ticket_number == "68-548-052"
    assert ticket.performer == "@Valeri_Nikulina"
    assert ticket.subject == "Проблемы МП Goodline Город/ЛК"
    assert ticket.messages[0].text.startswith("Услуга подключена")
    assert ticket.messages[-1].id == "assignment-2"
    assert ticket.messages[-1].author_role == "system"


def test_builds_ticket_from_chat_history_in_message_order(tmp_path: Path) -> None:
    fake = FakeMetabaseClient(
        [
            [_header()],
            [],
            [
                {
                    "ID": 10,
                    "ID_PROBLEM": 68548052,
                    "DT_BEGIN": "2026-09-07T15:19:56",
                    "DT_END": "2026-09-07T15:25:00",
                    "TYPE_MESSAGE": "Оператор",
                    "SENDER": "OPENFIRE",
                    "FIO_SENDER": "Лена",
                    "TEXT": "HIDDEN_FORM",
                },
                {
                    "ID": 11,
                    "ID_PROBLEM": 68548052,
                    "DT_BEGIN": "2026-09-07T15:19:56",
                    "DT_END": "2026-09-07T15:25:00",
                    "TYPE_MESSAGE": "Абонент",
                    "SENDER": None,
                    "FIO_SENDER": "",
                    "TEXT": "Авторизация по звонку прошла, но приложение снова просит номер телефона",
                },
                {
                    "ID": 12,
                    "ID_PROBLEM": 68548052,
                    "DT_BEGIN": "2026-09-07T15:19:56",
                    "DT_END": "2026-09-07T15:25:00",
                    "TYPE_MESSAGE": "Оператор",
                    "SENDER": "LOGINOV_GV",
                    "FIO_SENDER": "Логинов Герман Владимирович",
                    "TEXT": "Сначала уточните договор, затем попробуйте авторизоваться через SMS-код",
                },
            ],
        ]
    )
    source = MetabaseTicketSource(_settings(tmp_path), client_factory=lambda: fake)

    ticket = source.fetch_ticket(68548052, assignment_generation=1)

    actual = [message for message in ticket.messages if not str(message.id).startswith("assignment-")]
    assert [message.id for message in actual] == ["metabase-chat-11", "metabase-chat-12"]
    assert [message.author_role for message in actual] == ["subscriber", "operator"]
    assert "VW_CHAT_MESSAGES" in fake.queries[2]
    assert "ORDER BY ID" in fake.queries[2]
    assert TicketAnalyzer(KnowledgeStore(tmp_path)).analyze(ticket).category == "authentication"
