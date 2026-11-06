from __future__ import annotations

import re
from typing import Any


class Redactor:
    """Best-effort PII redaction before text leaves the internal boundary."""

    _patterns = (
        (re.compile(r"(?<!\d)(?:\+7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"), "[ТЕЛЕФОН]"),
        (re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), "[IP-АДРЕС]"),
        (re.compile(r"(?<!\d)\d{2}-\d{3}-\d{3}(?!\d)"), "[НОМЕР ТИКЕТА]"),
        (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}\b"), "[EMAIL]"),
        (re.compile(r"(?i)\b(?:логин|login|username)\s*[:=]\s*[@\w.\-]+"), "логин: [ЛОГИН]"),
        (re.compile(r"(?<!\w)@[A-Za-z0-9_]{3,}(?!\w)"), "@[ЛОГИН]"),
        (
            re.compile(r"(?i)\b(?:id_[a-z0-9_]+|client_id|abon_id)\s*[:=]\s*[\w.-]+"),
            "[ИДЕНТИФИКАТОР]",
        ),
        (
            re.compile(
                r"(?im)^\s*client_name\s*:\s*[А-ЯЁ][А-ЯЁа-яё-]+"
                r"(?:[ \t]+[А-ЯЁ][А-ЯЁа-яё-]+){0,2}"
            ),
            "client_name: [ФИО]",
        ),
        (re.compile(r"(?i)\b(?:фио|ф\.[ \t]*и\.[ \t]*о\.)[ \t]*[:=][ \t]*[А-ЯЁ][а-яё-]+(?:[ \t]+[А-ЯЁ][а-яё-]+){1,2}"), "ФИО: [ФИО]"),
        (re.compile(r"\b[А-ЯЁ][а-яё-]+[ \t]+[А-ЯЁ][а-яё-]+(?:ович|евич|овна|евна|ична)\b"), "[ФИО]"),
        (re.compile(r"\b[А-ЯЁ][а-яё-]+[ \t]+[А-ЯЁ][а-яё-]+[ \t]+[А-ЯЁ][а-яё-]+\b"), "[ФИО]"),
        (re.compile(r"\b[А-ЯЁ][а-яё-]{2,}[ \t]+[А-ЯЁ][а-яё-]{2,}\b"), "[ФИО]"),
        (re.compile(r"\b[А-ЯЁ][а-яё-]{2,}[ \t]+\[(?:ФИО|СОТРУДНИК)\]"), "[ФИО]"),
        (re.compile(r"(?i)\b(?:адрес)\s*[:=]\s*[^\n;]+"), "адрес: [АДРЕС]"),
        (
            re.compile(
                r"(?i)\b(?:[А-ЯЁа-яё-]+\s+){1,3}"
                r"(?:улица|ул\.?|проспект|пр-т|переулок|пер\.?|шоссе|бульвар|набережная|наб\.?)"
                r"\s*,?\s*\d+(?:\s*[-/]\s*\d+)?"
            ),
            "[АДРЕС]",
        ),
        (
            re.compile(
                r"(?i)(?:г(?:ород)?\.?\s+[А-ЯЁа-яё-]+[,\s]+)?"
                r"(?:по\s+адресу\s+)?(?:ул(?:ица)?|проспект|пр-т|переулок|шоссе|бульвар|набережная)\.?\s+"
                r"[А-ЯЁа-яё0-9-]+(?:\s+[А-ЯЁа-яё0-9-]+){0,3}\s*,?\s*"
                r"(?:д(?:ом)?\.?\s*)?\d+(?:\s*[-/]\s*\d+)?"
                r"(?:\s*,?\s*(?:п(?:одъезд)?|кв(?:артира)?|д(?:ом)?)\.?\s*\d+)*"
            ),
            "[АДРЕС]",
        ),
        (
            re.compile(
                r"(?i)(?:по\s+адресу\s+)?(?:ул(?:ица)?|проспект|пр-т|переулок|шоссе|бульвар|набережная)\.?\s+"
                r"[А-ЯЁа-яё0-9-]+(?:\s+[А-ЯЁа-яё0-9-]+){0,3}\s*,?\s*\d+"
                r"(?:\s*[-/]\s*\d+)?(?:\s*,?\s*(?:п(?:одъезд)?|кв(?:артира)?|д(?:ом)?)\.?\s*\d+)*"
            ),
            "[АДРЕС]",
        ),
        (
            re.compile(
                r"(?i)\b(?:[А-ЯЁа-яё-]+\s+){1,3}(?:ул|пр|пер|наб)\.\s*,?\s*\d+"
                r"(?:\s*[-/]\s*\d+)?(?:\s*,?\s*(?:п(?:одъезд)?|кв(?:артира)?|д(?:ом)?)\.?\s*\d+)*"
            ),
            "[АДРЕС]",
        ),
        (
            re.compile(r"\b[А-ЯЁ][а-яё-]{3,}\s*,\s*\d{1,4}(?:\s*[-/]\s*\d{1,4})?\b"),
            "[АДРЕС]",
        ),
        (
            re.compile(
                r"\b[А-ЯЁ][а-яё-]{3,}\s+\d{1,4}(?:\s*[-/]\s*\d{1,4})?"
                r"(?:\s*,\s*\d+\s*(?:подъезд|кв(?:артира)?|дом))\b",
                re.IGNORECASE,
            ),
            "[АДРЕС]",
        ),
        (
            re.compile(r"(?i)\b(?:с|по|на)\s+адрес(?:а|у|е)?\s+[А-ЯЁ][а-яё-]+"),
            "[АДРЕС]",
        ),
        (
            re.compile(
                r"\[АДРЕС\]\s*,?\s*(?:[А-ЯЁа-яё-]{2,}\.?\s*,?\s*)?"
                r"\d{1,4}[А-ЯЁа-яё]?(?:\s*[-/]\s*\d+)?",
                re.IGNORECASE,
            ),
            "[АДРЕС]",
        ),
        (re.compile(r"(?<!\d)\d{6,12}(?!\d)"), "[ИДЕНТИФИКАТОР]"),
    )

    _sensitive_keys = {
        "fio",
        "full_name",
        "name",
        "phone",
        "address",
        "login",
        "username",
        "performer",
        "id_abon",
        "id_client",
        "id_house",
        "id_contract",
        "client_id",
        "abon_id",
        "subscriber",
        "ticket_id",
        "ticket_number",
    }

    def redact_text(self, text: str) -> str:
        redacted = text
        for pattern, replacement in self._patterns:
            redacted = pattern.sub(replacement, redacted)
        return redacted

    def for_external_llm(self, value: Any) -> Any:
        """Recursively remove internal identifiers and redact free text."""
        if isinstance(value, dict):
            return {
                key: self.for_external_llm(item)
                for key, item in value.items()
                if key.lower() not in self._sensitive_keys
            }
        if isinstance(value, list):
            return [self.for_external_llm(item) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        return value
