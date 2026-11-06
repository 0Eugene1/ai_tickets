import json
from pathlib import Path

from app.knowledge.runtime_audit import audit_runtime


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_runtime_audit_checks_safety_and_retrieval(tmp_path: Path) -> None:
    _write(
        tmp_path / "confluence-test.jsonl",
        [
            {
                "id": "rk",
                "title": "Родительский контроль не отображается",
                "category": "parental_control",
                "symptoms": "Услуга активна, но приложение предлагает подключить",
                "resolution": "Не подключать повторно, проверить статус",
                "source": "confluence:test",
                "confirmed": True,
                "automatic_answer_allowed": False,
            }
        ],
    )

    report = audit_runtime(tmp_path)

    assert report["passed"] is True
    assert report["privacy_and_safety"]["issues"] == 0
    assert report["confluence_retrieval"]["self_query_top1"] == 1


def test_runtime_audit_reports_unsafe_record_without_echoing_value(tmp_path: Path) -> None:
    _write(
        tmp_path / "telegram-test.jsonl",
        [
            {
                "id": "unsafe",
                "symptoms": "Позвонить 8 999 123-45-67",
                "resolution": "Ответ",
                "confirmed": False,
                "automatic_answer_allowed": True,
            }
        ],
    )

    report = audit_runtime(tmp_path)

    assert report["passed"] is False
    issue = report["privacy_and_safety"]["issue_records"][0]
    assert issue["id"] == "unsafe"
    assert "phone" in issue["markers"]
    assert "8 999 123-45-67" not in json.dumps(issue, ensure_ascii=False)
