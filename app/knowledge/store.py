from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.models.ticket import SimilarCase


_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")
_STOP_WORDS = {
    "абонент",
    "абонента",
    "абона",
    "данных",
    "добрый",
    "день",
    "есть",
    "идентификатор",
    "как",
    "можно",
    "пожалуйста",
    "привет",
    "приложение",
    "приложении",
    "приложения",
    "проблема",
    "проблемы",
    "сотрудник",
    "тикет",
    "тикета",
    "фио",
    "что",
    "client",
    "name",
}
_RU_SUFFIXES = (
    "иями",
    "ями",
    "ами",
    "ого",
    "ему",
    "ому",
    "иях",
    "ах",
    "ях",
    "ий",
    "ый",
    "ая",
    "ое",
    "ые",
    "ов",
    "ев",
    "ам",
    "ям",
    "ом",
    "ем",
    "у",
    "ю",
    "а",
    "я",
    "ы",
    "и",
    "е",
    "о",
)

# A full morphological engine is excessive for the MVP, but these domain roots
# prevent common inflections (``звонок``/``звонит``, ``домофон``/``домофонный``)
# from hiding an otherwise exact support case.
_DOMAIN_ROOTS = (
    "архив",
    "двер",
    "домофон",
    "звон",
    "камер",
    "контрол",
    "ноч",
    "номер",
    "отключ",
    "подключ",
    "приход",
    "прочит",
    "разреш",
    "сбо",
    "скач",
    "созда",
    "уведомл",
    "исчез",
    "знач",
)


class KnowledgeStore:
    """Small file-backed knowledge base suitable for the MVP."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _tokens(text: str) -> set[str]:
        tokens: set[str] = set()
        for raw_word in _WORD.findall(text):
            word = raw_word.lower()
            # Confluence and Telegram use both Latin ``SMS`` and Cyrillic ``СМС``.
            # Treat them as one token so a short operator query can find either spelling.
            if word in {"sms", "смс"}:
                word = "sms"
            if word.startswith(("оплат", "платеж", "платёж", "попол")):
                word = "оплат"
            if word.startswith(("долг", "задолж")):
                word = "долг"
            if word in _STOP_WORDS or word.isdigit():
                continue
            for root in _DOMAIN_ROOTS:
                if word.startswith(root):
                    word = root
                    break
            for suffix in _RU_SUFFIXES:
                if len(word) - len(suffix) >= 4 and word.endswith(suffix):
                    word = word[: -len(suffix)]
                    break
            tokens.add(word)
        return tokens

    def _documents(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        documents: list[dict[str, Any]] = []
        for file_path in sorted(self.path.glob("*.jsonl")):
            for line in file_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if item.get("confirmed") is True and item.get("retrieval_allowed") is not False:
                        documents.append(item)
        for file_path in sorted(self.path.glob("*.md")):
            text = file_path.read_text(encoding="utf-8").strip()
            if text:
                documents.append(
                    {
                        "id": file_path.stem,
                        "title": text.splitlines()[0].lstrip("# "),
                        "symptoms": text,
                        "resolution": text,
                        "source": str(file_path),
                        "confirmed": True,
                    }
                )
        return documents

    def search(
        self,
        query: str,
        limit: int = 3,
        category: str | None = None,
        *,
        strict_category: bool = True,
    ) -> list[SimilarCase]:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []
        matches: list[SimilarCase] = []
        for item in self._documents():
            item_category = item.get("category")
            category_mismatch = bool(category and item_category not in (None, "", category))
            if category_mismatch and strict_category:
                continue
            haystack = " ".join(
                str(item.get(key, "")) for key in ("title", "category", "symptoms", "resolution")
            )
            tokens = self._tokens(haystack)
            overlap = query_tokens & tokens
            if not overlap:
                continue
            if len(query_tokens) >= 3 and len(overlap) < 2:
                continue
            query_coverage = len(overlap) / len(query_tokens)
            jaccard = len(overlap) / max(1, len(query_tokens | tokens))
            title_tokens = self._tokens(str(item.get("title", "")))
            title_coverage = len(query_tokens & title_tokens) / len(query_tokens)
            score = 0.7 * query_coverage + 0.15 * jaccard + 0.15 * title_coverage
            if category_mismatch:
                # Classification is a useful hint, not an absolute boundary: real
                # tickets often mention an account, a camera and a door together.
                score *= 0.95
            elif category and item_category == category:
                # A concrete domain detected in the ticket should beat a generic
                # cross-domain workflow with similar wording (for example an RK
                # status mismatch versus a generic callback request).
                score += 0.08
            if score < 0.15:
                continue
            matches.append(
                SimilarCase(
                    case_id=str(item.get("id", "unknown")),
                    title=str(item.get("title", "Подтверждённый случай")),
                    resolution=str(item.get("resolution", "")),
                    source=str(item.get("source", "local knowledge base")),
                    score=round(min(score, 1.0), 4),
                    required_checks=[str(value) for value in item.get("required_checks", [])],
                    automatic_answer_allowed=bool(item.get("automatic_answer_allowed", False)),
                )
            )
        return sorted(matches, key=lambda item: item.score, reverse=True)[:limit]
