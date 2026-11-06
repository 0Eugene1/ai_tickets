from __future__ import annotations

import argparse
import html
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.knowledge.store import KnowledgeStore


def temporal_evaluation(
    export_path: Path,
    knowledge_path: Path,
    cutoff: datetime,
    output_dir: Path,
) -> dict[str, Any]:
    export = json.loads(export_path.read_text(encoding="utf-8-sig"))
    dates = {item.get("id"): str(item.get("date", "")) for item in export.get("messages", [])}
    records = [
        json.loads(line)
        for line in knowledge_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    train: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    for record in records:
        started_at = min(
            (
                dates.get(message_id, "")
                for message_id in record.get("message_ids", [])
                if dates.get(message_id)
            ),
            default="",
        )
        enriched = {**record, "thread_started_at": started_at}
        if started_at and datetime.fromisoformat(started_at) >= cutoff:
            holdout.append(enriched)
        else:
            train.append(enriched)

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as directory:
        temporary_store = Path(directory)
        (temporary_store / "training.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in train),
            encoding="utf-8",
        )
        store = KnowledgeStore(temporary_store)
        for record in holdout:
            matches = store.search(record["symptoms"], limit=3, category=record["category"])
            results.append(
                {
                    "id": record["id"],
                    "date": record["thread_started_at"],
                    "category": record["category"],
                    "problem": record["symptoms"],
                    "actual_expert_answer": record["resolution"],
                    "matches": [match.model_dump(mode="json") for match in matches],
                }
            )

    summary = {
        "cutoff": cutoff.isoformat(),
        "training_records": len(train),
        "holdout_records": len(holdout),
        "holdout_with_matches": sum(bool(item["matches"]) for item in results),
        "holdout_without_matches": sum(not item["matches"] for item in results),
        "note": "Relevance must be reviewed by a person; lexical presence is not semantic correctness.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "temporal-evaluation.json").write_text(
        json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    cards: list[str] = []
    for item in results:
        matches = "".join(
            f'<section class="match"><b>{html.escape(match["case_id"])}</b> · score {match["score"]:.3f}'
            f'<pre>{html.escape(match["resolution"])}</pre>'
            f'<small>{html.escape(match["source"])}</small></section>'
            for match in item["matches"]
        ) or (
            '<div class="none">Похожих старых решений не найдено — '
            "это корректный безопасный результат.</div>"
        )
        cards.append(
            f'<article><div class="meta">{html.escape(item["date"])} · '
            f'{html.escape(item["category"])} · {html.escape(item["id"])}</div>'
            f'<h2>Новый вопрос (контрольная выборка)</h2>'
            f'<pre>{html.escape(item["problem"])}</pre>'
            f'<h2>Фактический ответ эксперта</h2>'
            f'<pre class="actual">{html.escape(item["actual_expert_answer"])}</pre>'
            f'<h2>Что нашлось в старой базе</h2>{matches}</article>'
        )
    document = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Временная проверка базы знаний</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#f4f6f8;color:#18212b;margin:0}}header{{background:#fff;position:sticky;top:0;padding:18px 24px;box-shadow:0 2px 9px #0002}}main{{max-width:1100px;margin:20px auto;padding:0 16px}}article{{background:#fff;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 2px 8px #0001}}h1{{margin:0 0 7px}}h2{{font-size:15px;margin:18px 0 6px}}pre{{white-space:pre-wrap;font:14px/1.45 Segoe UI,Arial;background:#f7f9fb;padding:11px;border-radius:8px}}.actual{{border-left:4px solid #198754}}.match{{border-left:4px solid #2878c8;padding:10px 12px;margin:10px 0;background:#f8fbff}}.meta,small{{color:#687582}}.none{{background:#fff3cd;padding:12px;border-radius:8px}}
</style></head><body><header><h1>Временная проверка базы знаний</h1><div>Обучающая история: {len(train)} · Новые случаи: {len(holdout)} · С найденными аналогами: {summary["holdout_with_matches"]}</div><div>Период проверки начинается с {html.escape(cutoff.date().isoformat())}. Найденные совпадения требуют оценки человеком.</div></header><main>{''.join(cards)}</main></body></html>"""
    (output_dir / "temporal-evaluation.html").write_text(document, encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate knowledge retrieval on a later time period")
    parser.add_argument("export", type=Path)
    parser.add_argument("knowledge", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--cutoff", required=True, type=datetime.fromisoformat)
    args = parser.parse_args()
    summary = temporal_evaluation(args.export, args.knowledge, args.cutoff, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
