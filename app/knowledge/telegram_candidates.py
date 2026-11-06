from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.analyzers.classifier import classify_text
from app.privacy.redactor import Redactor


def render_text(message: dict[str, Any]) -> str:
    value = message.get("text", "")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(
            part if isinstance(part, str) else str(part.get("text", ""))
            for part in value
        ).strip()
    return str(value).strip()


class DisjointSet:
    def __init__(self, values: list[int]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _roster_redactor(messages: list[dict[str, Any]]) -> re.Pattern[str] | None:
    names = {
        str(message.get("from", "")).strip()
        for message in messages
        if len(str(message.get("from", "")).strip()) >= 4
    }
    if not names:
        return None
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(alternatives, re.IGNORECASE)


def _safe_text(text: str, redactor: Redactor, roster_pattern: re.Pattern[str] | None) -> str:
    text = redactor.redact_text(text)
    if roster_pattern:
        text = roster_pattern.sub("[СОТРУДНИК]", text)
    return redactor.redact_text(text)


def _partition_answer(text: str) -> tuple[list[str], list[str]]:
    """Separate reusable statements from clarification questions."""
    resolution: list[str] = []
    required_checks: list[str] = []
    clarification_start = re.compile(
        r"^(?:а\s+|и\s+)?(?:подскажи|уточни|пришли|скинь|можешь|можно|какой|какая|какие|когда|"
        r"где|что\s+за|есть\s+ли|пробовали|проверяли|воспроизвели|получилось\s+ли)\b",
        re.IGNORECASE,
    )
    for line in (part.strip() for part in text.splitlines()):
        if not line:
            continue
        if line.endswith("?") or clarification_start.search(line):
            required_checks.append(line)
        else:
            resolution.append(line)
    return resolution, required_checks


def _is_status_only(answer: str) -> bool:
    lowered = answer.casefold()
    words = re.findall(r"[^\W\d_]+", lowered, re.UNICODE)
    normalized = " ".join(words)
    hard_non_answers = (
        "не обращай внимания",
        "по жипу этого абонента ответил",
    )
    if any(normalized.startswith(phrase) for phrase in hard_non_answers):
        return True
    domain_markers = (
        "адрес",
        "авторизац",
        "архив",
        "баготряск",
        "банк",
        "биллинг",
        "верс",
        "двер",
        "договор",
        "камер",
        "код",
        "консул",
        "номер",
        "обнов",
        "оплат",
        "прилож",
        "пульт",
        "разрешен",
        "розыгрыш",
        "сайт",
        "смс",
        "тариф",
        "трубк",
        "уведомлен",
        "услуг",
        "шаринг",
    )
    if any(marker in lowered for marker in domain_markers):
        return False
    status_markers = (
        "смотр",
        "глян",
        "погляд",
        "выясня",
        "передал",
        "передам",
        "перекин",
        "забрал",
        "взял в работу",
        "поправ",
        "исправ",
        "сделаем",
        "готово",
        "завтра",
        "следующ",
        "в процессе",
        "ответственн",
        "задач",
        "разбира",
        "новост",
        "обратной связи",
        "в работе",
        "пока не нашли",
    )
    return any(marker in lowered for marker in status_markers)


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_review_html(
    path: Path,
    knowledge: list[dict[str, Any]],
    ambiguous: list[dict[str, Any]],
) -> None:
    categories = sorted({str(item.get("category", "other_mplk")) for item in knowledge})
    options = "".join(f'<option value="{html.escape(category)}">{html.escape(category)}</option>' for category in categories)
    cards: list[str] = []
    for item in knowledge:
        checks = "".join(f"<li>{html.escape(check)}</li>" for check in item.get("required_checks", [])) or "<li>нет</li>"
        search = " ".join((item["symptoms"], item["resolution"], item["category"])).casefold()
        cards.append(
            f'<article class="card" data-kind="source-linked" data-category="{html.escape(item["category"])}" '
            f'data-search="{html.escape(search, quote=True)}">'
            f'<div class="meta"><span class="badge ok">вопрос ↔ ответ эксперта</span><span class="badge">{html.escape(item["category"])}</span>'
            f'<span>{html.escape(item["id"])}</span></div>'
            f'<h3>Проблема</h3><pre>{html.escape(item["symptoms"])}</pre>'
            f'<h3>Ответ эксперта — смысл ещё не проверен</h3><pre>{html.escape(item["resolution"])}</pre>'
            f'<h3>Что уточнить</h3><ul>{checks}</ul>'
            f'<div class="source">Источник: {html.escape(item["source"])}</div></article>'
        )
    for item in ambiguous:
        question = item.get("question") or item.get("possible_question_context") or ""
        answer = item.get("possible_answer") or ""
        search = " ".join((str(question), str(answer), str(item.get("reason", "")))).casefold()
        cards.append(
            f'<article class="card ambiguous" data-kind="ambiguous" data-category="" data-search="{html.escape(search, quote=True)}">'
            f'<div class="meta"><span class="badge warn">нужна проверка</span><span>{html.escape(item["id"])}</span></div>'
            f'<div class="reason">Причина: {html.escape(item.get("reason", ""))}</div>'
            f'<h3>Возможный вопрос</h3><pre>{html.escape(str(question))}</pre>'
            f'<h3>Возможный ответ</h3><pre>{html.escape(str(answer))}</pre>'
            f'<div class="source">Источник: {html.escape(item["source"])}</div></article>'
        )
    document = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка базы знаний СЛК/МПЛК</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f4f6f8;color:#17202a}}header{{position:sticky;top:0;background:#fff;padding:18px 24px;box-shadow:0 2px 10px #0002;z-index:2}}h1{{margin:0 0 12px;font-size:22px}}.summary{{color:#52606d;margin-bottom:12px}}.filters{{display:flex;gap:10px;flex-wrap:wrap}}input,select{{padding:9px 12px;border:1px solid #bac3cc;border-radius:8px;min-width:220px}}main{{max-width:1100px;margin:20px auto;padding:0 16px}}.card{{background:#fff;border-radius:12px;padding:18px;margin:14px 0;border-left:5px solid #198754;box-shadow:0 2px 8px #0001}}.card.ambiguous{{border-left-color:#e0a800}}.meta{{display:flex;gap:9px;align-items:center;color:#64748b;font-size:13px}}.badge{{background:#e8eef5;border-radius:99px;padding:4px 9px}}.badge.ok{{background:#d9f7e7;color:#146c43}}.badge.warn{{background:#fff0c2;color:#7a5800}}h3{{font-size:14px;margin:16px 0 5px}}pre{{white-space:pre-wrap;font:14px/1.45 Segoe UI,Arial,sans-serif;background:#f8fafc;padding:11px;border-radius:7px}}.source,.reason{{font-size:13px;color:#64748b;margin-top:12px}}.hidden{{display:none}}#visible{{font-weight:600}}
</style></head><body><header><h1>База знаний СЛК/МПЛК</h1>
<div class="summary">Связь вопрос–ответ найдена: {len(knowledge)} · Сомнительные связи: {len(ambiguous)} · Показано: <span id="visible">0</span><br>Важно: автор и Reply подтверждены, но правильность и применимость решения ещё должен проверить ИИ или человек.</div>
<div class="filters"><input id="query" placeholder="Поиск по проблеме или ответу"><select id="kind"><option value="source-linked">Связанные ответы экспертов</option><option value="ambiguous">Сомнительные связи</option><option value="all">Все</option></select><select id="category"><option value="">Все категории</option>{options}</select></div></header>
<main>{''.join(cards)}</main>
<script>const q=document.querySelector('#query'),kind=document.querySelector('#kind'),cat=document.querySelector('#category'),cards=[...document.querySelectorAll('.card')],visible=document.querySelector('#visible');function apply(){{let n=0;const text=q.value.toLocaleLowerCase('ru');for(const card of cards){{const show=(kind.value==='all'||card.dataset.kind===kind.value)&&(!cat.value||card.dataset.category===cat.value)&&(!text||card.dataset.search.includes(text));card.classList.toggle('hidden',!show);if(show)n++}}visible.textContent=n}}q.addEventListener('input',apply);kind.addEventListener('change',apply);cat.addEventListener('change',apply);apply();</script>
</body></html>"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(path)


def extract_candidates(
    export_path: Path,
    output_dir: Path,
    answer_author_ids: set[str],
) -> dict[str, Any]:
    raw_bytes = export_path.read_bytes()
    export = json.loads(raw_bytes.decode("utf-8-sig"))
    messages = [
        item
        for item in export.get("messages", [])
        if item.get("type") == "message" and isinstance(item.get("id"), int)
    ]
    messages.sort(key=lambda item: (str(item.get("date", "")), item["id"]))
    by_id = {item["id"]: item for item in messages}
    index_by_id = {item["id"]: index for index, item in enumerate(messages)}
    redactor = Redactor()
    roster_pattern = _roster_redactor(messages)

    sets = DisjointSet(list(by_id))
    for message in messages:
        parent_id = message.get("reply_to_message_id")
        if parent_id in by_id:
            sets.union(message["id"], parent_id)

    components: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for message in messages:
        components[sets.find(message["id"])].append(message)

    trusted_counts = Counter(
        str(message.get("from", ""))
        for message in messages
        if str(message.get("from_id", "")) in answer_author_ids
    )
    candidate_records: list[dict[str, Any]] = []
    ambiguous_records: list[dict[str, Any]] = []
    used_answer_ids: set[int] = set()
    exact_hashes: set[str] = set()
    duplicate_count = 0

    for component in components.values():
        component.sort(key=lambda item: index_by_id[item["id"]])
        has_direct_trusted_reply = any(
            str(message.get("from_id", "")) in answer_author_ids
            and message.get("reply_to_message_id") in by_id
            and str(by_id[message["reply_to_message_id"]].get("from_id", "")) not in answer_author_ids
            for message in component
        )
        if not has_direct_trusted_reply:
            continue
        question_messages = [
            message
            for message in component
            if str(message.get("from_id", "")) not in answer_author_ids and render_text(message)
        ]
        answer_messages = [
            message
            for message in component
            if str(message.get("from_id", "")) in answer_author_ids and render_text(message)
        ]
        if not question_messages or not answer_messages:
            continue
        question = "\n\n".join(
            _safe_text(render_text(message), redactor, roster_pattern)
            for message in question_messages
        )
        answer_parts: list[str] = []
        required_checks: list[str] = []
        for message in answer_messages:
            safe_answer = _safe_text(render_text(message), redactor, roster_pattern)
            message_answers, message_checks = _partition_answer(safe_answer)
            answer_parts.extend(message_answers)
            required_checks.extend(message_checks)
        answer = "\n".join(answer_parts)
        used_answer_ids.update(message["id"] for message in answer_messages)
        source_ids = [message["id"] for message in component]
        source = f"telegram-thread:{min(source_ids)}"
        if len(re.sub(r"\W", "", answer)) < 10 or _is_status_only(answer):
            ambiguous_records.append(
                {
                    "id": f"ambiguous-thread-{min(source_ids)}",
                    "reason": (
                        "trusted reply is operational status without reusable technical content"
                        if _is_status_only(answer)
                        else "trusted reply has no reusable statement after separating clarification questions"
                    ),
                    "question": question,
                    "possible_answer": answer,
                    "required_checks": required_checks,
                    "source": source,
                    "message_ids": source_ids,
                    "confirmed": False,
                }
            )
            continue
        content_hash = hashlib.sha256(f"{question}\0{answer}".casefold().encode("utf-8")).hexdigest()
        if content_hash in exact_hashes:
            duplicate_count += 1
            continue
        exact_hashes.add(content_hash)
        category = classify_text(question).category
        candidate_records.append(
            {
                "id": f"telegram-{min(source_ids)}",
                "title": f"Кандидат из ответа доверенного эксперта ({category})",
                "category": category,
                "symptoms": question,
                "resolution": answer,
                "required_checks": required_checks,
                "source": source,
                "message_ids": source_ids,
                "answer_message_ids": [message["id"] for message in answer_messages],
                "confirmed": True,
                "source_link_confirmed": True,
                "semantic_status": "unverified_candidate",
                "automatic_answer_allowed": False,
                "validation_basis": "trusted answer author and explicit Telegram reply link; semantic applicability is not verified",
                "confirmed_at": datetime.now(timezone.utc).date().isoformat(),
            }
        )

    for message in messages:
        if str(message.get("from_id", "")) not in answer_author_ids or message["id"] in used_answer_ids:
            continue
        answer = _safe_text(render_text(message), redactor, roster_pattern)
        if not answer:
            reason = "answer has no text"
        elif message.get("reply_to_message_id") not in by_id:
            reason = "answer has no resolvable reply link"
        else:
            reason = "reply chain does not contain a question from another author"
        position = index_by_id[message["id"]]
        preceding = [
            candidate
            for candidate in messages[max(0, position - 3):position]
            if str(candidate.get("from_id", "")) not in answer_author_ids and render_text(candidate)
        ]
        ambiguous_records.append(
            {
                "id": f"ambiguous-answer-{message['id']}",
                "reason": reason,
                "possible_question_context": "\n\n".join(
                    _safe_text(render_text(item), redactor, roster_pattern) for item in preceding
                ),
                "possible_answer": answer,
                "source": f"telegram-message:{message['id']}",
                "message_ids": [message["id"]],
                "confirmed": False,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "knowledge.jsonl", candidate_records)
    _write_jsonl(output_dir / "ambiguous.jsonl", ambiguous_records)
    _write_review_html(output_dir / "review.html", candidate_records, ambiguous_records)
    with (output_dir / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("id", "category", "symptoms", "resolution", "required_checks", "source", "source_link_confirmed", "semantic_status", "automatic_answer_allowed", "review_override"),
        )
        writer.writeheader()
        for record in candidate_records:
            writer.writerow(
                {
                    "id": record["id"],
                    "category": record["category"],
                    "symptoms": record["symptoms"],
                    "resolution": record["resolution"],
                    "required_checks": "\n".join(record["required_checks"]),
                    "source": record["source"],
                    "source_link_confirmed": "yes",
                    "semantic_status": "unverified_candidate",
                    "automatic_answer_allowed": "no",
                    "review_override": "",
                }
            )

    progress = {
        "completed": True,
        "stage": "source_linked_candidate_extraction",
        "source": str(export_path),
        "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "processed_message_count": len(messages),
        "answer_author_ids": sorted(answer_author_ids),
        "answer_messages_by_exported_name": dict(trusted_counts),
        "knowledge_records": len(candidate_records),
        "ambiguous_records": len(ambiguous_records),
        "exact_duplicates_skipped": duplicate_count,
        "non_reusable_status_records": sum(
            item.get("reason") == "trusted reply is operational status without reusable technical content"
            for item in ambiguous_records
        ),
        "outputs": {
            "knowledge": str(output_dir / "knowledge.jsonl"),
            "ambiguous": str(output_dir / "ambiguous.jsonl"),
            "review": str(output_dir / "review.csv"),
            "review_html": str(output_dir / "review.html"),
        },
        "assumptions": [
            "Configured answer-author IDs are trusted by the chat owner.",
            "A reusable pair requires an explicit Telegram reply edge from a trusted author.",
            "Unlinked and unresolved answer messages are never auto-confirmed.",
            "A confirmed source link does not prove that the response is a complete or reusable solution.",
            "Automatic answers remain disabled until semantic verification succeeds.",
        ],
    }
    temporary_progress = output_dir / "progress.json.tmp"
    temporary_progress.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_progress.replace(output_dir / "progress.json")
    return progress


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract trusted Telegram Q&A threads into a reviewable KB")
    parser.add_argument("export", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--answer-author-id", action="append", required=True, dest="answer_author_ids")
    args = parser.parse_args()
    progress = extract_candidates(args.export, args.output_dir, set(args.answer_author_ids))
    print(json.dumps(progress, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
