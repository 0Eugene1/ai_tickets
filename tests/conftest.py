from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def ticket_payload() -> dict:
    return {
        "ticket_id": 68548052,
        "ticket_number": "68-548-052",
        "subject_id": 0,
        "subject": "Проблемы МП Goodline Город/ЛК",
        "group_id": 0,
        "performer": "team-user",
        "status": 0,
        "created_at": "2026-09-07T15:17:00+07:00",
        "updated_at": "2026-09-07T17:23:00+07:00",
        "subscriber": {"id_abon": 1993109},
        "messages": [
            {
                "id": 1,
                "created_at": "2026-09-07T15:20:00+07:00",
                "author_role": "operator",
                "scope": "private",
                "text": "Родительский контроль подключён, но в приложении не отображается",
            }
        ],
        "diagnostics": {
            "platform": "Android",
            "app_version": None,
            "os_version": None,
            "bugshaker_found": False,
            "checks": [],
        },
    }


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_path=tmp_path / "tasks.db",
        knowledge_path=Path("data/knowledge"),
        llm_provider="mock",
        telegram_enabled=False,
        metabase_polling_enabled=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client
