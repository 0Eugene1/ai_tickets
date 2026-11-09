from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


OUTPUT_NAMES = {
    "telegram": "telegram-knowledge.jsonl",
    "confluence": "confluence-knowledge.jsonl",
    "manual": "manual-knowledge.jsonl",
}


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    temporary.replace(path)


def _source_kind(path: Path) -> str:
    if path.name.startswith("telegram"):
        return "telegram"
    if path.name.startswith("confluence-"):
        return "confluence"
    if path.name.startswith("manual-"):
        return "manual"
    raise ValueError(f"Unknown knowledge source file: {path.name}")


def build_runtime_bundle(
    source_dir: Path, output_dir: Path, *, reviewed_dir: Path | None = None
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {kind: [] for kind in OUTPUT_NAMES}
    seen_ids: set[str] = set()

    source_paths = sorted(source_dir.glob("*.jsonl"))
    if reviewed_dir is not None:
        if not reviewed_dir.is_dir():
            raise ValueError(f"Reviewed knowledge directory not found: {reviewed_dir}")
        # Rebuild manual cards from their tracked source, including removals.
        source_paths = [path for path in source_paths if not path.name.startswith("manual-")]
        source_paths.extend(sorted(reviewed_dir.glob("*.jsonl")))

    for source_path in source_paths:
        kind = _source_kind(source_path)
        for line_number, line in enumerate(
            source_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            record = json.loads(line)
            record_id = str(record.get("id", ""))
            if not record_id:
                raise ValueError(f"Missing id in {source_path.name}:{line_number}")
            if record_id in seen_ids:
                raise ValueError(f"Duplicate knowledge id: {record_id}")
            if record.get("confirmed") is not True:
                raise ValueError(f"Unconfirmed record cannot enter runtime: {record_id}")
            if record.get("retrieval_allowed") is False:
                raise ValueError(f"Retrieval is disabled for record: {record_id}")
            if record.get("automatic_answer_allowed") is not False:
                raise ValueError(f"Automatic answers must be disabled: {record_id}")
            seen_ids.add(record_id)
            grouped[kind].append(record)

    output_dir.mkdir(parents=True, exist_ok=True)
    expected_paths = {output_dir / name for name in OUTPUT_NAMES.values()}
    unexpected = set(output_dir.glob("*.jsonl")) - expected_paths
    if unexpected:
        names = ", ".join(sorted(path.name for path in unexpected))
        raise ValueError(f"Unexpected runtime knowledge files: {names}")

    for kind, name in OUTPUT_NAMES.items():
        if kind != "manual" or grouped[kind] or reviewed_dir is not None:
            _write_jsonl(output_dir / name, grouped[kind])

    return {
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "files": 2 + int(bool(grouped["manual"]) or reviewed_dir is not None),
        "telegram_cards": len(grouped["telegram"]),
        "confluence_cards": len(grouped["confluence"]),
        "manual_cards": len(grouped["manual"]),
        "total_cards": sum(len(records) for records in grouped.values()),
        "automatic_answer_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build runtime knowledge from confirmed records"
    )
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--reviewed-dir", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            build_runtime_bundle(args.source_dir, args.output_dir, reviewed_dir=args.reviewed_dir),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
