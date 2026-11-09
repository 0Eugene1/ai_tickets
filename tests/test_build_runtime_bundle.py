import json
from pathlib import Path

import pytest

from app.knowledge.build_runtime_bundle import build_runtime_bundle
from app.knowledge.store import KnowledgeStore


def _write(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def _record(record_id: str) -> dict:
    return {
        "id": record_id,
        "title": record_id,
        "symptoms": "Проблема",
        "resolution": "Решение",
        "confirmed": True,
        "automatic_answer_allowed": False,
    }


def test_builds_exactly_two_runtime_knowledge_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "runtime"
    source.mkdir()
    _write(source / "telegram.jsonl", _record("tg-1"))
    _write(source / "confluence-batch-001.jsonl", _record("cf-1"))

    summary = build_runtime_bundle(source, output)

    assert summary["total_cards"] == 2
    assert sorted(path.name for path in output.glob("*.jsonl")) == [
        "confluence-knowledge.jsonl",
        "telegram-knowledge.jsonl",
    ]


def test_rejects_unconfirmed_runtime_record(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    record = _record("unsafe")
    record["confirmed"] = False
    _write(source / "telegram.jsonl", record)

    with pytest.raises(ValueError, match="Unconfirmed record"):
        build_runtime_bundle(source, tmp_path / "runtime")


def test_reviewed_case_is_retrievable_and_removed_on_rebuild(tmp_path: Path) -> None:
    source = tmp_path / "source"
    reviewed = tmp_path / "reviewed"
    output = tmp_path / "runtime"
    source.mkdir()
    reviewed.mkdir()
    _write(source / "telegram.jsonl", _record("tg-1"))
    card = Path(__file__).resolve().parents[1] / "data/knowledge/reviewed/manual-194209.jsonl"
    record = json.loads(card.read_text(encoding="utf-8"))
    reviewed_card = reviewed / "manual-194209.jsonl"
    _write(reviewed_card, record)

    summary = build_runtime_bundle(source, output, reviewed_dir=reviewed)
    matches = KnowledgeStore(output).search("NV501 пульт кнопка ОК не выбирает букву")

    assert summary["manual_cards"] == 1
    assert matches[0].case_id == record["id"]
    assert "UHDX" in matches[0].resolution
    assert matches[0].automatic_answer_allowed is False
    reviewed_card.unlink()
    build_runtime_bundle(output, output, reviewed_dir=reviewed)
    assert record["id"] not in {doc["id"] for doc in KnowledgeStore(output)._documents()}
    assert {doc["id"] for doc in KnowledgeStore(output)._documents()} == {"tg-1"}


@pytest.mark.parametrize("blocked_field", ["confirmed", "retrieval_allowed"])
def test_blocks_pending_or_retrieval_disabled_manual_case(
    tmp_path: Path, blocked_field: str
) -> None:
    source = tmp_path / "source"
    reviewed = tmp_path / "reviewed"
    source.mkdir()
    reviewed.mkdir()
    record = _record("manual-blocked")
    record[blocked_field] = False
    _write(reviewed / "manual-blocked.jsonl", record)

    with pytest.raises(ValueError):
        build_runtime_bundle(source, tmp_path / "runtime", reviewed_dir=reviewed)
