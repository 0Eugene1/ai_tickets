from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERDICTS = {"approved", "partial", "rejected"}
ANSWER_TYPES = {
    "solution",
    "diagnostic",
    "routing",
    "information",
    "known_issue",
    "clarification",
    "status",
    "unclear",
}

CATEGORY_ALIASES = {
    "mplk_authorization": "authentication",
    "mplk_accounts": "contract_selection",
    "door_calls": "intercom_calls",
    "camera_access": "camera_archive",
    "antivirus": "other_mplk",
    "equipment_rental": "other_mplk",
    "mplk_btv_remote": "device",
    "btv_parental_pin": "parental_control",
    "mplk_service_status": "service_display",
}


def _retrieval_category(value: Any) -> str:
    category = str(value or "other_mplk")
    return CATEGORY_ALIASES.get(category, category)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False)
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_decision(decision: dict[str, Any]) -> None:
    if decision.get("verdict") not in VERDICTS:
        raise ValueError(f"Invalid verdict for {decision.get('id')}: {decision.get('verdict')}")
    if decision.get("answer_type") not in ANSWER_TYPES:
        raise ValueError(
            f"Invalid answer_type for {decision.get('id')}: {decision.get('answer_type')}"
        )
    for field in ("problem_summary", "answer_summary", "reason"):
        if not str(decision.get(field, "")).strip():
            raise ValueError(f"Missing {field} for {decision.get('id')}")


def _render_html(path: Path, records: list[dict[str, Any]], progress: dict[str, Any]) -> None:
    cards: list[str] = []
    for record in records:
        verdict = record["semantic_status"]
        validation = record["semantic_validation"]
        search = " ".join(
            str(value)
            for value in (
                record.get("problem_summary", ""),
                record.get("answer_summary", ""),
                record.get("category", ""),
                validation.get("reason", ""),
            )
        ).casefold()
        conditions = "".join(
            f"<li>{html.escape(str(value))}</li>" for value in record.get("conditions", [])
        ) or "<li>не выделены</li>"
        cards.append(
            f'<article class="card {verdict}" data-verdict="{verdict}" '
            f'data-search="{html.escape(search, quote=True)}">'
            f'<div class="meta"><span class="badge">{html.escape(verdict)}</span>'
            f'<span>{html.escape(record["answer_type"])}</span>'
            f'<span>{html.escape(record.get("category", ""))}</span>'
            f'<span>{html.escape(record["id"])}</span></div>'
            f'<h3>Проблема — нормализовано</h3><p>{html.escape(record["problem_summary"])}</p>'
            f'<h3>Ответ/действие — нормализовано</h3><p>{html.escape(record["answer_summary"])}</p>'
            f'<h3>Условия применения</h3><ul>{conditions}</ul>'
            f'<h3>Почему выставлена такая оценка</h3><p>{html.escape(validation["reason"])}</p>'
            f'<details><summary>Исходная обезличенная связка</summary>'
            f'<h4>Вопрос</h4><pre>{html.escape(record.get("symptoms", ""))}</pre>'
            f'<h4>Ответ эксперта</h4><pre>{html.escape(record.get("resolution", ""))}</pre>'
            f'</details></article>'
        )

    counts = progress["verdict_counts"]
    document = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Смысловая проверка базы знаний</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#f4f6f8;color:#17202a;margin:0}}header{{position:sticky;top:0;background:#fff;padding:18px 24px;box-shadow:0 2px 10px #0002;z-index:2}}main{{max-width:1100px;margin:20px auto;padding:0 16px}}.filters{{display:flex;gap:10px;flex-wrap:wrap}}input,select{{padding:9px 12px;border:1px solid #bac3cc;border-radius:8px;min-width:230px}}.card{{background:#fff;border-left:5px solid #198754;border-radius:10px;padding:18px;margin:14px 0;box-shadow:0 2px 8px #0001}}.card.partial{{border-left-color:#e0a800}}.card.rejected{{border-left-color:#c0392b}}.meta{{display:flex;gap:9px;flex-wrap:wrap;color:#64748b;font-size:13px}}.badge{{background:#e8eef5;border-radius:99px;padding:4px 9px}}h3{{font-size:14px;margin:15px 0 5px}}p,pre{{font-size:14px;line-height:1.45}}pre{{white-space:pre-wrap;background:#f8fafc;padding:11px;border-radius:7px}}.hidden{{display:none}}
</style></head><body><header><h1>Смысловая проверка базы знаний</h1><p>Пригодные: {counts.get("approved", 0)} · Частичные: {counts.get("partial", 0)} · Отклонённые: {counts.get("rejected", 0)} · Не проверено: {progress["missing_count"]} · Показано: <span id="visible">0</span></p><p>Это проверка ИИ Codex, а не подтверждение человеком. Автоматические ответы абоненту запрещены.</p><div class="filters"><input id="query" placeholder="Поиск"><select id="verdict"><option value="all">Все</option><option value="approved">Пригодные</option><option value="partial">Частичные</option><option value="rejected">Отклонённые</option></select></div></header><main>{''.join(cards)}</main><script>const q=document.querySelector('#query'),v=document.querySelector('#verdict'),cards=[...document.querySelectorAll('.card')],visible=document.querySelector('#visible');function apply(){{let n=0;const text=q.value.toLocaleLowerCase('ru');for(const c of cards){{const show=(v.value==='all'||c.dataset.verdict===v.value)&&(!text||c.dataset.search.includes(text));c.classList.toggle('hidden',!show);if(show)n++}}visible.textContent=n}}q.addEventListener('input',apply);v.addEventListener('change',apply);apply();</script></body></html>"""
    path.write_text(document, encoding="utf-8")


def build_validation(
    source_path: Path,
    decisions_path: Path,
    output_dir: Path,
    *,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    source_bytes = source_path.read_bytes()
    source = _read_jsonl(source_path)
    decisions = _read_jsonl(decisions_path)
    source_by_id = {str(record["id"]): record for record in source}
    decision_by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        record_id = str(decision.get("id", ""))
        if record_id in decision_by_id:
            raise ValueError(f"Duplicate decision: {record_id}")
        if record_id not in source_by_id:
            raise ValueError(f"Unknown decision id: {record_id}")
        _validate_decision(decision)
        decision_by_id[record_id] = decision

    missing_ids = sorted(set(source_by_id) - set(decision_by_id))
    if missing_ids and not allow_incomplete:
        raise ValueError(f"Missing {len(missing_ids)} decisions; first: {missing_ids[:5]}")

    reviewed: list[dict[str, Any]] = []
    for record in source:
        decision = decision_by_id.get(str(record["id"]))
        if not decision:
            continue
        verdict = decision["verdict"]
        reviewed.append(
            {
                **record,
                "problem_summary": decision["problem_summary"],
                "answer_summary": decision["answer_summary"],
                "answer_type": decision["answer_type"],
                "conditions": [str(value) for value in decision.get("conditions", [])],
                "semantic_status": verdict,
                "retrieval_allowed": verdict == "approved",
                "automatic_answer_allowed": False,
                "confirmed": verdict == "approved",
                "semantic_validation": {
                    "reason": decision["reason"],
                    "reviewed_by": "Codex AI semantic review",
                    "human_reviewed": False,
                    "reviewed_at": datetime.now(timezone.utc).date().isoformat(),
                },
            }
        )

    groups = {
        verdict: [record for record in reviewed if record["semantic_status"] == verdict]
        for verdict in ("approved", "partial", "rejected")
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "all-reviewed.jsonl", reviewed)
    for verdict, records in groups.items():
        _write_jsonl(output_dir / f"{verdict}.jsonl", records)
    retrieval_records = [
        {
            "id": record["id"],
            "title": record.get("title", "Проверенный случай"),
            "category": _retrieval_category(record.get("category")),
            "symptoms": record["problem_summary"],
            "resolution": record["answer_summary"],
            "required_checks": record.get("conditions", []),
            "source": record.get("source", "semantic-validation"),
            "source_url": record.get("source_url"),
            "source_urls": record.get("source_urls", []),
            "source_page_id": record.get("source_page_id"),
            "source_page_ids": record.get("source_page_ids", []),
            "source_version": record.get("source_version"),
            "source_updated_at": record.get("source_updated_at"),
            "message_ids": record.get("message_ids", []),
            "confirmed": True,
            "source_link_confirmed": record.get("source_link_confirmed", False),
            "semantic_status": "approved",
            "retrieval_allowed": True,
            "automatic_answer_allowed": False,
        }
        for record in groups["approved"]
    ]
    _write_jsonl(output_dir / "retrieval" / "knowledge.jsonl", retrieval_records)

    progress = {
        "completed": not missing_ids,
        "source": str(source_path),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_records": len(source),
        "reviewed_records": len(reviewed),
        "missing_count": len(missing_ids),
        "missing_ids": missing_ids,
        "verdict_counts": dict(Counter(record["semantic_status"] for record in reviewed)),
        "answer_type_counts": dict(Counter(record["answer_type"] for record in reviewed)),
        "human_reviewed": False,
        "automatic_answer_allowed": False,
        "retrieval_records": len(retrieval_records),
        "retrieval_path": str(output_dir / "retrieval" / "knowledge.jsonl"),
    }
    (output_dir / "progress.json").write_text(
        json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _render_html(output_dir / "review.html", reviewed, progress)
    return progress


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a semantically reviewed knowledge set")
    parser.add_argument("source", type=Path)
    parser.add_argument("decisions", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    progress = build_validation(
        args.source,
        args.decisions,
        args.output_dir,
        allow_incomplete=args.allow_incomplete,
    )
    print(json.dumps(progress, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
