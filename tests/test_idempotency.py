from copy import deepcopy

from fastapi.testclient import TestClient


def test_same_content_returns_same_task(client: TestClient, ticket_payload: dict) -> None:
    first = client.post("/api/v1/tickets/start", json=ticket_payload).json()
    repeated = deepcopy(ticket_payload)
    repeated["updated_at"] = "2026-09-07T18:00:00+07:00"
    second = client.post("/api/v1/tickets/start", json=repeated).json()

    assert second["task_id"] == first["task_id"]
    assert second["duplicate"] is True


def test_changed_message_creates_new_task(client: TestClient, ticket_payload: dict) -> None:
    first = client.post("/api/v1/tickets/start", json=ticket_payload).json()
    changed = deepcopy(ticket_payload)
    changed["messages"][0]["text"] += ". Дополнение оператора."
    second = client.post("/api/v1/tickets/start", json=changed).json()

    assert second["task_id"] != first["task_id"]
    assert second["duplicate"] is False
