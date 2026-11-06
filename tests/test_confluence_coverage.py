import json
from pathlib import Path

from app.knowledge.confluence_coverage import build_coverage


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_coverage_distinguishes_touched_from_fully_dispositioned(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.jsonl"
    batches = tmp_path / "batches"
    decisions = tmp_path / "document-decisions.jsonl"
    _write(
        candidates,
        [
            {"source_page_id": "1", "title": "One", "priority": "high"},
            {"source_page_id": "2", "title": "Two", "priority": "medium"},
        ],
    )
    _write(batches / "batch-001" / "source.jsonl", [{"id": "card", "source_page_id": "1"}])
    _write(
        batches / "batch-001" / "decisions.jsonl",
        [{"id": "card", "verdict": "approved"}],
    )
    _write(decisions, [{"source_page_id": "2", "status": "excluded_not_relevant"}])

    summary = build_coverage(candidates, batches, decisions, tmp_path / "out")

    assert summary["documents_touched"] == 1
    assert summary["documents_fully_dispositioned"] == 1
    assert summary["documents_remaining_until_complete"] == 1


def test_old_documents_are_excluded_by_freshness_policy(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.jsonl"
    _write(
        candidates,
        [
            {
                "source_page_id": "old",
                "title": "Эксперимент: скидка на тариф",
                "updated_at": "2022-05-01",
                "priority": "medium",
            },
            {
                "source_page_id": "tech",
                "title": "Диагностика звонков домофона",
                "updated_at": "2022-05-01",
                "priority": "medium",
            },
        ],
    )

    summary = build_coverage(
        candidates,
        tmp_path / "batches",
        tmp_path / "decisions.jsonl",
        tmp_path / "out",
    )

    rows = [json.loads(line) for line in (tmp_path / "out" / "documents.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["status"] == "policy_stale_excluded"
    assert rows[1]["status"] == "policy_age_excluded"
    assert summary["documents_fully_dispositioned"] == 2
