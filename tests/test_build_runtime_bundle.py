import json
from pathlib import Path

import pytest

from app.knowledge.build_runtime_bundle import build_runtime_bundle


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
