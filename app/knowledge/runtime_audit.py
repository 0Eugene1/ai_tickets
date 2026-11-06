from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.knowledge.store import KnowledgeStore


_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
)
_IP_ADDRESS = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_CREDENTIAL = re.compile(
    r"(?i)(?:password|passwd|парол\w*|login|логин\w*|token|api[_ -]?key|secret)\s*[:=]\s*\S+"
)


def _read_runtime_records(path: Path) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    for source_path in sorted(path.glob("*.jsonl")):
        for line in source_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append((source_path, json.loads(line)))
    return records


def audit_runtime(knowledge_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    records = _read_runtime_records(knowledge_path)
    store = KnowledgeStore(knowledge_path)
    issues: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()

    for source_path, record in records:
        serialized = json.dumps(record, ensure_ascii=False)
        markers: list[str] = []
        for marker, pattern in (
            ("email", _EMAIL),
            ("phone", _PHONE),
            ("ip_address", _IP_ADDRESS),
            ("credential_assignment", _CREDENTIAL),
        ):
            if pattern.search(serialized):
                markers.append(marker)
        if record.get("confirmed") is not True:
            markers.append("not_confirmed")
        if record.get("automatic_answer_allowed") is not False:
            markers.append("automatic_answer_not_disabled")
        if markers:
            issue_counts.update(markers)
            issues.append(
                {
                    "file": source_path.name,
                    "id": str(record.get("id", "unknown")),
                    "markers": markers,
                }
            )

    confluence_records = [
        record for source_path, record in records if source_path.name.startswith("confluence-")
    ]
    retrieval_misses: list[dict[str, Any]] = []
    retrieval_top1 = 0
    retrieval_top3 = 0
    for record in confluence_records:
        matches = store.search(
            str(record.get("symptoms", "")),
            category=str(record.get("category", "")) or None,
            strict_category=False,
        )
        match_ids = [match.case_id for match in matches]
        record_id = str(record.get("id", "unknown"))
        retrieval_top1 += bool(match_ids and match_ids[0] == record_id)
        retrieval_top3 += record_id in match_ids
        if record_id not in match_ids:
            retrieval_misses.append({"id": record_id, "returned_ids": match_ids})

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "knowledge_path": str(knowledge_path),
        "files": len({source_path for source_path, _record in records}),
        "cards": len(records),
        "telegram_cards": sum(
            source_path.name.startswith("telegram") for source_path, _record in records
        ),
        "confluence_cards": len(confluence_records),
        "privacy_and_safety": {
            "passed": not issues,
            "issues": len(issues),
            "issue_counts": dict(issue_counts),
            "issue_records": issues,
        },
        "confluence_retrieval": {
            "cards_checked": len(confluence_records),
            "self_query_top1": retrieval_top1,
            "self_query_top3": retrieval_top3,
            "misses": retrieval_misses,
        },
    }
    report["passed"] = (
        report["privacy_and_safety"]["passed"]
        and retrieval_top3 == len(confluence_records)
    )
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the runtime knowledge directory")
    parser.add_argument("knowledge_path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = audit_runtime(args.knowledge_path, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
