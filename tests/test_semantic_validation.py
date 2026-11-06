import json
from pathlib import Path

import pytest

from app.knowledge.semantic_validation import _retrieval_category, build_validation


def test_retrieval_categories_are_aligned_with_classifier() -> None:
    assert _retrieval_category("mplk_authorization") == "authentication"
    assert _retrieval_category("door_calls") == "intercom_calls"
    assert _retrieval_category("camera_access") == "camera_archive"
    assert _retrieval_category("mplk_btv_remote") == "device"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_builds_separate_reviewed_sets_and_requires_all_decisions(tmp_path: Path) -> None:
    source = tmp_path / "knowledge.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    _write_jsonl(
        source,
        [
            {"id": "one", "symptoms": "Проблема 1", "resolution": "Ответ 1", "confirmed": True},
            {"id": "two", "symptoms": "Проблема 2", "resolution": "Ответ 2", "confirmed": True},
        ],
    )
    _write_jsonl(
        decisions,
        [
            {
                "id": "one",
                "verdict": "approved",
                "answer_type": "solution",
                "problem_summary": "Первая проблема",
                "answer_summary": "Первое решение",
                "reason": "Ответ применим",
            }
        ],
    )

    partial = build_validation(source, decisions, tmp_path / "partial", allow_incomplete=True)
    assert partial["reviewed_records"] == 1
    assert partial["missing_count"] == 1
    approved = json.loads((tmp_path / "partial" / "approved.jsonl").read_text(encoding="utf-8"))
    assert approved["retrieval_allowed"] is True
    assert approved["automatic_answer_allowed"] is False
    assert approved["semantic_validation"]["human_reviewed"] is False
    retrieval = json.loads(
        (tmp_path / "partial" / "retrieval" / "knowledge.jsonl").read_text(encoding="utf-8")
    )
    assert retrieval["symptoms"] == "Первая проблема"
    assert retrieval["resolution"] == "Первое решение"
    assert "semantic_validation" not in retrieval

    with pytest.raises(ValueError, match="Missing 1 decisions"):
        build_validation(source, decisions, tmp_path / "complete")
