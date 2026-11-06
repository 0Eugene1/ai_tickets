import json
from pathlib import Path

from app.knowledge.telegram_loader import load_confirmed_pairs


def test_loader_uses_only_explicit_confirmations(tmp_path: Path) -> None:
    export = {
        "messages": [
            {"id": 1, "type": "message", "text": "Телефон +7 923 123-45-67, не работает услуга"},
            {"id": 2, "type": "message", "text": "соседнее, но не решение"},
            {"id": 3, "type": "message", "text": "Проверенное решение"},
        ]
    }
    confirmations = [
        {"question_id": 1, "solution_id": 3, "confirmed": True},
        {"question_id": 1, "solution_id": 2, "confirmed": False},
    ]
    export_path = tmp_path / "result.json"
    confirmations_path = tmp_path / "confirmed.json"
    export_path.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")
    confirmations_path.write_text(json.dumps(confirmations, ensure_ascii=False), encoding="utf-8")

    records = load_confirmed_pairs(export_path, confirmations_path)

    assert len(records) == 1
    assert records[0]["resolution"] == "Проверенное решение"
    assert "923" not in records[0]["symptoms"]
