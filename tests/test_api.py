from fastapi.testclient import TestClient
from copy import deepcopy


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ticket_flow(client: TestClient, ticket_payload: dict) -> None:
    started = client.post("/api/v1/tickets/start", json=ticket_payload)
    assert started.status_code == 202
    task_id = started.json()["task_id"]

    status = client.get(f"/api/v1/tickets/status/{task_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"

    response = client.get(f"/api/v1/tickets/result/{task_id}")
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["ticket_id"] == ticket_payload["ticket_id"]
    assert result["category"] == "parental_control"
    assert "Требуется проверка сотрудником" in result["report_markdown"]
    assert all("source" in item for item in result["evidence"])
    assert result["confidence"] == "medium"


def test_missing_task(client: TestClient) -> None:
    assert client.get("/api/v1/tickets/status/missing").status_code == 404


def test_empty_ticket_uses_subject_without_false_antivirus_label(
    client: TestClient, ticket_payload: dict
) -> None:
    payload = deepcopy(ticket_payload)
    payload["ticket_id"] = 68580860
    payload["ticket_number"] = "68-580-860"
    payload["subject"] = "Проблема со входом ЛК/МПЛК/МП Goodline Город"
    payload["messages"] = []

    started = client.post("/api/v1/tickets/start", json=payload).json()
    result = client.get(f"/api/v1/tickets/result/{started['task_id']}").json()["result"]

    assert result["category"] == "authentication"
    assert result["category_label"] == "МПЛК → Авторизация"
    assert "Антивирус" not in result["report_markdown"]
    assert "нет содержательного описания" in result["summary"]
