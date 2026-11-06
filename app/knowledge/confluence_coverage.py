from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COMPLETE_STATUSES = {
    "reviewed_complete",
    "excluded_not_relevant",
    "stale_needs_update",
    "superseded_by_current_doc",
    "policy_stale_excluded",
    "policy_age_excluded",
}

STALE_COMMERCIAL_TITLE_MARKERS = (
    "акци",
    "бесплатн",
    "изменение стоимости",
    "изменение тариф",
    "коммерческ",
    "пакет канал",
    "приставка",
    "продаж",
    "роутер",
    "скидк",
    "спец.тариф",
    "спецтариф",
    "тариф",
    "тп ",
    'тп "',
    "эксперимент",
)


def _policy_disposition(candidate: dict[str, Any]) -> tuple[str, str] | None:
    updated_at = str(candidate.get("updated_at", ""))
    title = str(candidate.get("title", "")).casefold()
    if updated_at[:4] and updated_at[:4] < "2024" and any(
        marker in title for marker in STALE_COMMERCIAL_TITLE_MARKERS
    ):
        return (
            "policy_stale_excluded",
            "Коммерческий документ старше 2024 года: тарифы, цены и акции требуют актуального каталога.",
        )
    if updated_at[:4] and updated_at[:4] < "2024":
        return (
            "policy_age_excluded",
            "Документ старше 2024 года не допускается в ответы без подтверждения свежим источником или владельцем процесса.",
        )
    return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _source_page_ids(record: dict[str, Any]) -> set[str]:
    values = {str(value) for value in record.get("source_page_ids", []) if value}
    if record.get("source_page_id"):
        values.add(str(record["source_page_id"]))
    return values


def build_coverage(
    candidates_path: Path,
    batches_root: Path,
    document_decisions_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    candidates = _read_jsonl(candidates_path)
    document_decisions = {
        str(record["source_page_id"]): record for record in _read_jsonl(document_decisions_path)
    }
    touched: dict[str, list[dict[str, str]]] = defaultdict(list)

    for source_path in sorted(batches_root.glob("batch-*/source.jsonl")):
        decisions_path = source_path.parent / "decisions.jsonl"
        decisions = {str(row["id"]): row for row in _read_jsonl(decisions_path)}
        for source_record in _read_jsonl(source_path):
            card_id = str(source_record["id"])
            decision = decisions.get(card_id, {})
            for page_id in _source_page_ids(source_record):
                touched[page_id].append(
                    {
                        "batch": source_path.parent.name,
                        "card_id": card_id,
                        "verdict": str(decision.get("verdict", "missing")),
                    }
                )

    records: list[dict[str, Any]] = []
    for candidate in candidates:
        page_id = str(candidate["source_page_id"])
        manual = document_decisions.get(page_id)
        if manual:
            status = str(manual["status"])
            note = str(manual.get("note", ""))
        elif policy := _policy_disposition(candidate):
            status, note = policy
        elif page_id in touched:
            status = "in_progress"
            note = "Есть проверенные карточки, но весь документ ещё не закрыт."
        else:
            status = "not_reviewed"
            note = ""
        records.append(
            {
                "source_page_id": page_id,
                "title": candidate.get("title", ""),
                "space_key": candidate.get("space_key", ""),
                "priority": candidate.get("priority", ""),
                "updated_at": candidate.get("updated_at", ""),
                "source_url": candidate.get("source_url", ""),
                "status": status,
                "note": note,
                "cards": touched.get(page_id, []),
            }
        )

    status_counts = Counter(record["status"] for record in records)
    touched_count = sum(bool(record["cards"]) for record in records)
    fully_dispositioned = sum(record["status"] in COMPLETE_STATUSES for record in records)
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_documents": len(records),
        "documents_with_cards": touched_count,
        "documents_without_cards": len(records) - touched_count,
        # Backward-compatible names retained for existing report consumers.
        "documents_touched": touched_count,
        "documents_untouched": len(records) - touched_count,
        "documents_fully_dispositioned": fully_dispositioned,
        "documents_remaining_until_complete": len(records) - fully_dispositioned,
        "status_counts": dict(status_counts),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "documents.jsonl", records)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    rows = []
    for record in records:
        cards = "<br>".join(
            html.escape(f"{card['batch']}: {card['card_id']} [{card['verdict']}]")
            for card in record["cards"]
        ) or "—"
        rows.append(
            "<tr>"
            f"<td>{html.escape(record['status'])}</td>"
            f"<td>{html.escape(record['priority'])}</td>"
            f"<td>{html.escape(record['space_key'])}</td>"
            f"<td><a href=\"{html.escape(record['source_url'], quote=True)}\">{html.escape(record['title'])}</a></td>"
            f"<td>{html.escape(record['updated_at'][:10])}</td>"
            f"<td>{html.escape(record['note'])}</td>"
            f"<td>{cards}</td></tr>"
        )
    document = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Покрытие Confluence</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#17202a}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d5dce3;padding:8px;vertical-align:top}}th{{background:#edf2f7;position:sticky;top:0}}tr:nth-child(even){{background:#f8fafc}}code{{background:#edf2f7;padding:2px 5px;border-radius:4px}}
</style></head><body><h1>Покрытие документов Confluence</h1><p>Кандидатов: <b>{len(records)}</b> · Создана хотя бы одна карточка: <b>{touched_count}</b> · Без карточек, но с решением об исключении/замене: <b>{len(records) - touched_count}</b> · Полностью закрыто решением: <b>{fully_dispositioned}</b> · Осталось до полного покрытия: <b>{len(records) - fully_dispositioned}</b></p><p>Статусы: <code>{html.escape(json.dumps(dict(status_counts), ensure_ascii=False))}</code></p><table><thead><tr><th>Статус</th><th>Приоритет</th><th>Раздел</th><th>Документ</th><th>Обновлён</th><th>Комментарий</th><th>Карточки</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    (output_dir / "review.html").write_text(document, encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Confluence semantic review coverage")
    parser.add_argument("candidates", type=Path)
    parser.add_argument("batches_root", type=Path)
    parser.add_argument("document_decisions", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    summary = build_coverage(
        args.candidates, args.batches_root, args.document_decisions, args.output_dir
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
