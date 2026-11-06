from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
from typing import Any


SPACE_WEIGHT = {"SSG": 3, "DMPLK": 2, "MOBP": 2, "FQAA": 1, "callcenter": 1}

SUPPORT_SIGNALS = (
    "вопрос",
    "ответ",
    "инструкц",
    "руководств",
    "диагност",
    "проблем",
    "ошиб",
    "не работает",
    "не отображ",
    "недоступ",
    "провер",
    "решени",
    "сценар",
    "алгоритм",
    "тест-кейс",
    "smoke",
    "чек-лист",
    "критери приемк",
    "функционал",
)

PRODUCT_SIGNALS = (
    "мплк",
    "мобильн",
    "личн кабинет",
    "goodline город",
    "авторизац",
    "регистрац",
    "аккаунт",
    "оплат",
    "тариф",
    "услуг",
    "подключ",
    "отключ",
    "родительск",
    "домофон",
    "камер",
    "чат",
    "уведомлен",
    "пульт",
    "роутер",
)

PROJECT_ADMIN_SIGNALS = (
    "еженедельный отчет",
    "премиальн",
    "смета проект",
    "установочное совещание",
    "собрания с заказчиком",
    "матрица рисков",
    "дневник проект",
    "базовый план проект",
    "план работ",
    "планирование ресурсов",
    "паспорт проект",
    "укрупненная иср",
    "матрица распределения",
)

STALE_LABEL_SIGNALS = ("архив", "проверить_актуальность", "устар")

SENSITIVE_PATTERNS = {
    "login_password_table": re.compile(
        r"(?i)(?:логин|username)\s*(?:[/|:=-]|и)?\s*(?:пароль|password)"
    ),
    "password_assignment": re.compile(r"(?im)^\s*(?:пароль|password)\s*[:=]\s*\S+"),
    "token_assignment": re.compile(
        r"(?im)^\s*(?:api[_ -]?key|api[_ -]?token|access[_ -]?token|secret)\s*[:=]\s*\S+"
    ),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "privileged_inline_credential": re.compile(
        r"(?i)\b(?:root|admin)\s+(?=\S{8,})(?=\S*[a-z])(?=\S*\d)\S+"
    ),
    "test_account_pin": re.compile(
        r"(?is)(?:тестов(?:ый|ые)\s+(?:аккаунт|пользовател)|тестовые данные)"
        r".{0,600}?(?:код|pin|парол)\s*[:=-]?\s*\d{4,}"
    ),
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]


def _jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(record, ensure_ascii=False)
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
        + "\n"
        for record in records
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_jsonl(records), encoding="utf-8")
    temporary.replace(path)


def score_document(record: dict[str, Any]) -> tuple[int, list[str], str | None]:
    text = str(record.get("text", "")).strip()
    title = str(record.get("title", "")).casefold()
    preview = text[:5000].casefold()
    labels = " ".join(str(value) for value in record.get("labels", [])).casefold()
    if not text:
        return 0, [], "empty"
    if len(text) < 100:
        return 0, [], "placeholder_or_redirect"

    score = SPACE_WEIGHT.get(str(record.get("space_key", "")), 0)
    reasons = [f"space:{record.get('space_key', '')}"]
    if len(text) >= 500:
        score += 1
        reasons.append("substantial_text")
    if len(text) >= 3000:
        score += 1
        reasons.append("detailed_text")

    title_support = [signal for signal in SUPPORT_SIGNALS if signal in title]
    title_product = [signal for signal in PRODUCT_SIGNALS if signal in title]
    body_support = [signal for signal in SUPPORT_SIGNALS if signal in preview]
    body_product = [signal for signal in PRODUCT_SIGNALS if signal in preview]
    if title_support:
        score += min(8, 3 * len(title_support))
        reasons.append("title_support:" + ",".join(title_support[:4]))
    if title_product:
        score += min(6, 2 * len(title_product))
        reasons.append("title_product:" + ",".join(title_product[:4]))
    if body_support:
        score += min(4, len(body_support))
        reasons.append("body_support:" + ",".join(body_support[:4]))
    if body_product:
        score += min(3, len(body_product))
        reasons.append("body_product:" + ",".join(body_product[:4]))

    admin = [signal for signal in PROJECT_ADMIN_SIGNALS if signal in title]
    if admin:
        score -= 8
        reasons.append("project_admin:" + ",".join(admin[:3]))
    stale = [signal for signal in STALE_LABEL_SIGNALS if signal in labels or signal in title]
    if stale:
        score -= 3
        reasons.append("needs_freshness_check:" + ",".join(stale[:3]))

    return score, reasons, None


def _priority(score: int) -> str:
    if score >= 12:
        return "high"
    if score >= 8:
        return "medium"
    return "low"


def sensitive_markers(text: str) -> list[str]:
    return [name for name, pattern in SENSITIVE_PATTERNS.items() if pattern.search(text)]


def _render_review(path: Path, candidates: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> None:
    cards: list[str] = []
    for record in candidates:
        search = " ".join(
            (
                str(record.get("space_key", "")),
                str(record.get("title", "")),
                str(record.get("text", ""))[:1500],
            )
        ).casefold()
        snippet = str(record.get("text", ""))[:900]
        cards.append(
            f'<article class="card {record["priority"]}" data-priority="{record["priority"]}" '
            f'data-space="{html.escape(str(record.get("space_key", "")), quote=True)}" '
            f'data-search="{html.escape(search, quote=True)}">'
            f'<div class="meta"><span>{html.escape(str(record.get("space_key", "")))}</span>'
            f'<span>оценка {record["relevance_score"]}</span>'
            f'<span>{record["priority"]}</span><span>{record["text_length"]} знаков</span></div>'
            f'<h2>{html.escape(str(record.get("title", "")))}</h2>'
            f'<p class="reasons">{html.escape(" · ".join(record.get("selection_reasons", [])))}</p>'
            f'<pre>{html.escape(snippet)}</pre>'
            f'<a href="{html.escape(str(record.get("source_url", "")), quote=True)}">Открыть источник</a>'
            f'</article>'
        )
    excluded_counts = Counter(str(item.get("exclusion_reason", "other")) for item in excluded)
    spaces = sorted({str(item.get("space_key", "")) for item in candidates})
    options = "".join(f'<option value="{html.escape(value)}">{html.escape(value)}</option>' for value in spaces)
    document = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Кандидаты Confluence</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#f4f6f8;color:#17202a;margin:0}}header{{position:sticky;top:0;background:#fff;padding:16px 24px;box-shadow:0 2px 10px #0002;z-index:2}}main{{max-width:1050px;margin:20px auto;padding:0 16px}}.filters{{display:flex;gap:10px;flex-wrap:wrap}}input,select{{padding:9px 12px;border:1px solid #bac3cc;border-radius:8px;min-width:210px}}.card{{background:#fff;border-left:5px solid #1f6feb;border-radius:10px;padding:18px;margin:14px 0;box-shadow:0 2px 8px #0001}}.card.medium{{border-left-color:#d29922}}.card.low{{border-left-color:#8b949e}}.meta{{display:flex;gap:10px;flex-wrap:wrap;color:#64748b;font-size:13px}}h2{{font-size:18px}}pre{{white-space:pre-wrap;background:#f8fafc;padding:11px;border-radius:7px;max-height:260px;overflow:auto}}.reasons{{color:#64748b;font-size:13px}}.hidden{{display:none}}
</style></head><body><header><h1>Кандидаты из Confluence</h1><p>Отобрано: {len(candidates)} · Исключено: {len(excluded)} ({html.escape(str(dict(excluded_counts)))}) · Показано: <span id="visible">0</span></p><p>Это только предварительный отбор. Автоматические ответы запрещены.</p><div class="filters"><input id="query" placeholder="Поиск"><select id="space"><option value="all">Все разделы</option>{options}</select><select id="priority"><option value="all">Все приоритеты</option><option value="high">high</option><option value="medium">medium</option><option value="low">low</option></select></div></header><main>{''.join(cards)}</main><script>const q=document.querySelector('#query'),s=document.querySelector('#space'),p=document.querySelector('#priority'),cards=[...document.querySelectorAll('.card')],visible=document.querySelector('#visible');function apply(){{let n=0;const text=q.value.toLocaleLowerCase('ru');for(const c of cards){{const show=(s.value==='all'||c.dataset.space===s.value)&&(p.value==='all'||c.dataset.priority===p.value)&&(!text||c.dataset.search.includes(text));c.classList.toggle('hidden',!show);if(show)n++}}visible.textContent=n}}[q,s,p].forEach(x=>x.addEventListener(x===q?'input':'change',apply));apply();</script></body></html>"""
    path.write_text(document, encoding="utf-8")


def build_candidates(source_path: Path, output_dir: Path, *, threshold: int = 6) -> dict[str, Any]:
    source = _read_jsonl(source_path)
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for record in source:
        markers = sensitive_markers(str(record.get("text", "")))
        score, reasons, hard_exclusion = score_document(record)
        common = {
            "id": record.get("id"),
            "source_page_id": record.get("source_page_id"),
            "title": record.get("title"),
            "space_key": record.get("space_key"),
            "space_name": record.get("space_name"),
            "source": record.get("source"),
            "source_url": record.get("source_url"),
            "version": record.get("version"),
            "updated_at": record.get("updated_at"),
            "labels": record.get("labels", []),
            "text_length": len(str(record.get("text", ""))),
            "relevance_score": score,
        }
        if markers:
            hard_exclusion = "sensitive_content"
            reasons = ["quarantined:" + ",".join(markers)]
        if hard_exclusion or score < threshold:
            excluded.append(
                {
                    **common,
                    "exclusion_reason": hard_exclusion or "low_relevance_score",
                    "selection_reasons": reasons,
                }
            )
            continue
        candidates.append(
            {
                **common,
                "text": record.get("text", ""),
                "selection_reasons": reasons,
                "priority": _priority(score),
                "confirmed": False,
                "semantic_status": "unverified_document",
                "automatic_answer_allowed": False,
            }
        )

    candidates.sort(
        key=lambda item: (-int(item["relevance_score"]), str(item["space_key"]), str(item["title"]))
    )
    excluded.sort(key=lambda item: (str(item["exclusion_reason"]), str(item["space_key"]), str(item["title"])))
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "candidates.jsonl", candidates)
    _write_jsonl(output_dir / "excluded.jsonl", excluded)
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source_path),
        "source_records": len(source),
        "threshold": threshold,
        "candidate_records": len(candidates),
        "excluded_records": len(excluded),
        "candidate_by_space": dict(Counter(str(item["space_key"]) for item in candidates)),
        "candidate_by_priority": dict(Counter(str(item["priority"]) for item in candidates)),
        "excluded_by_reason": dict(Counter(str(item["exclusion_reason"]) for item in excluded)),
        "automatic_answer_allowed": False,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sensitive_audit = [
        {
            "id": item.get("id"),
            "space_key": item.get("space_key"),
            "title": item.get("title"),
            "source_url": item.get("source_url"),
            "markers": item.get("selection_reasons", []),
        }
        for item in excluded
        if item.get("exclusion_reason") == "sensitive_content"
    ]
    (output_dir / "sensitive-audit.json").write_text(
        json.dumps(sensitive_audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _render_review(output_dir / "review.html", candidates, excluded)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Select Confluence documents for semantic review")
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--threshold", type=int, default=6)
    args = parser.parse_args()
    print(
        json.dumps(
            build_candidates(args.source, args.output_dir, threshold=args.threshold),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
