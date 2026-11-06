import json
from pathlib import Path

from app.knowledge.telegram_candidates import extract_candidates


def test_extracts_linked_trusted_answers_and_isolates_unlinked(tmp_path: Path) -> None:
    export = {
        "messages": [
            {"id": 1, "type": "message", "date": "2026-01-01T10:00:00", "from": "Operator", "from_id": "user1", "text": "Тикет 68-123-456, телефон +7 923 123-45-67: не проходит оплата"},
            {"id": 2, "type": "message", "date": "2026-01-01T10:01:00", "from": "Expert", "from_id": "expert1", "reply_to_message_id": 1, "text": "Проверить версию приложения и повторить операцию"},
            {"id": 3, "type": "message", "date": "2026-01-01T11:00:00", "from": "Operator", "from_id": "user1", "text": "Второй вопрос"},
            {"id": 4, "type": "message", "date": "2026-01-01T11:01:00", "from": "Expert", "from_id": "expert1", "text": "Ответ без reply"},
            {"id": 5, "type": "message", "date": "2026-01-01T12:00:00", "from": "Operator", "from_id": "user1", "text": "Третий вопрос"},
            {"id": 6, "type": "message", "date": "2026-01-01T12:01:00", "from": "Expert", "from_id": "expert1", "reply_to_message_id": 5, "text": "Какая версия приложения?"},
            {"id": 7, "type": "message", "date": "2026-01-01T13:00:00", "from": "Operator", "from_id": "user1", "text": "Все обращения выше взяты?"},
            {"id": 8, "type": "message", "date": "2026-01-01T13:01:00", "from": "Expert", "from_id": "expert1", "reply_to_message_id": 7, "text": "Смотрю как раз"},
        ]
    }
    export_path = tmp_path / "result.json"
    export_path.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")

    progress = extract_candidates(export_path, tmp_path / "output", {"expert1"})
    knowledge = [json.loads(line) for line in (tmp_path / "output" / "knowledge.jsonl").read_text(encoding="utf-8").splitlines()]
    ambiguous = [json.loads(line) for line in (tmp_path / "output" / "ambiguous.jsonl").read_text(encoding="utf-8").splitlines()]

    assert progress["knowledge_records"] == 1
    assert progress["ambiguous_records"] == 3
    assert progress["non_reusable_status_records"] == 1
    assert knowledge[0]["confirmed"] is True
    assert knowledge[0]["source_link_confirmed"] is True
    assert knowledge[0]["semantic_status"] == "unverified_candidate"
    assert knowledge[0]["automatic_answer_allowed"] is False
    assert "923" not in knowledge[0]["symptoms"]
    assert "68-123-456" not in knowledge[0]["symptoms"]
    assert ambiguous[0]["confirmed"] is False
