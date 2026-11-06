from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.integrations.metabase_diagnostics import MetabaseDiagnosticsProvider
from app.models.ticket import Subscriber, TicketInput


class FakeClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def run_native_select(self, database_id: int, query: str) -> list[dict]:
        assert database_id == 5
        self.queries.append(query)
        return self.rows


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "tasks.db",
        metabase_base_url="https://metabase.internal",
        metabase_api_token="secret",
        metabase_diagnostics_enabled=True,
        metabase_diagnostics_max_age_days=90,
    )


def _ticket() -> TicketInput:
    now = datetime.now(timezone.utc)
    return TicketInput(
        ticket_id=1,
        ticket_number="1",
        subject_id=1,
        subject="Авторизация",
        group_id=1,
        performer="manager",
        status=0,
        created_at=now,
        updated_at=now,
        subscriber=Subscriber(id_abon=12345),
    )


def test_enriches_ticket_from_recent_read_only_session(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    fake = FakeClient(
        [
            {
                "OS_DEVICE": "android",
                "OS_VERSION": "14",
                "DETAILS": '{"APP-VERSION": "5.6.0"}',
                "CREATED_AT": (now - timedelta(hours=1)).isoformat(),
                "UPDATED_AT": now.isoformat(),
                "CLOSED": "N",
            }
        ]
    )
    provider = MetabaseDiagnosticsProvider(
        _settings(tmp_path), client_factory=lambda: fake
    )

    enriched = provider.enrich(_ticket())

    assert enriched.diagnostics.platform == "Android"
    assert enriched.diagnostics.app_version == "5.6.0"
    assert enriched.diagnostics.os_version == "14"
    assert enriched.diagnostics.checks[-1].status == "ok"
    assert "SS_STAT_LK_SESSIONS" in fake.queries[0]
    assert "12345" in fake.queries[0]


def test_stale_session_is_reported_but_not_used_as_current_version(tmp_path: Path) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=365)
    fake = FakeClient(
        [
            {
                "OS_DEVICE": "android",
                "OS_VERSION": "10",
                "DETAILS": '{"APP-VERSION": "1.0.0"}',
                "CREATED_AT": old.isoformat(),
                "UPDATED_AT": old.isoformat(),
            }
        ]
    )
    provider = MetabaseDiagnosticsProvider(
        _settings(tmp_path), client_factory=lambda: fake
    )

    enriched = provider.enrich(_ticket())

    assert enriched.diagnostics.platform is None
    assert enriched.diagnostics.app_version is None
    assert enriched.diagnostics.os_version is None
    assert enriched.diagnostics.checks[-1].status == "unavailable"
