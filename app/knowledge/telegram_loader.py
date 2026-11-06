from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.privacy.redactor import Redactor


def _text(message: dict[str, Any]) -> str:
    value = message.get("text", "")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in value)
    return str(value)


def load_confirmed_pairs(
    export_path: Path,
    confirmations_path: Path,
    redactor: Redactor | None = None,
) -> list[dict[str, Any]]:
    """Build KB records only from pairs explicitly approved in a separate manifest."""
    redactor = redactor or Redactor()
    export = json.loads(export_path.read_text(encoding="utf-8"))
    confirmations = json.loads(confirmations_path.read_text(encoding="utf-8"))
    messages = {str(item["id"]): item for item in export.get("messages", []) if item.get("type") == "message"}

    records: list[dict[str, Any]] = []
    for pair in confirmations:
        if pair.get("confirmed") is not True:
            continue
        question = messages.get(str(pair["question_id"]))
        solution = messages.get(str(pair["solution_id"]))
        if question is None or solution is None:
            raise ValueError(f"Unknown message id in confirmed pair: {pair}")
        record = {
            "id": str(pair.get("id", f"telegram-{question['id']}-{solution['id']}")),
            "title": redactor.redact_text(str(pair.get("title", "Подтверждённый случай из Telegram"))),
            "category": str(pair.get("category", "other_mplk")),
            "symptoms": redactor.redact_text(_text(question)),
            "resolution": redactor.redact_text(_text(solution)),
            "source": f"telegram:{question['id']}->{solution['id']}",
            "confirmed": True,
            "confirmed_by": redactor.redact_text(str(pair.get("confirmed_by", "manual-review"))),
            "confirmed_at": pair.get("confirmed_at"),
        }
        records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Load manually confirmed Telegram question/solution pairs")
    parser.add_argument("export", type=Path, help="Telegram Desktop JSON export")
    parser.add_argument("confirmations", type=Path, help="Manual confirmation manifest")
    parser.add_argument("output", type=Path, help="Destination JSONL file")
    args = parser.parse_args()

    records = load_confirmed_pairs(args.export, args.confirmations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    print(f"Saved {len(records)} confirmed pairs to {args.output}")


if __name__ == "__main__":
    main()
