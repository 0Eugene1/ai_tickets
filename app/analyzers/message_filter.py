from __future__ import annotations

import re

from app.models.ticket import TicketMessage


_SERVICE_PATTERNS = re.compile(
    r"(?:смен(?:а|ил[аи]?)\s+(?:тем|исполнител)|перемещен|назначен|статус\s+(?:измен|смен)|"
    r"тикет\s+(?:создан|закрыт|открыт)|звонок\s+(?:начат|завершен)|"
    r"скрипт\s+обработки\s+тикетов|что\s+обязательно\s+указать\s+в\s+комментарии|"
    r"не\s+(?:все\s+)?успел[аи]\s+(?:все\s+)?отзвонить|"
    r"bugshaker\.goodline\.info/(?:storage|api)/)",
    re.IGNORECASE,
)


def split_messages(messages: list[TicketMessage]) -> tuple[list[TicketMessage], list[TicketMessage]]:
    meaningful: list[TicketMessage] = []
    service: list[TicketMessage] = []
    for message in messages:
        if message.author_role.lower() in {"system", "bot", "service"} or _SERVICE_PATTERNS.search(message.text):
            service.append(message)
        else:
            meaningful.append(message)
    return meaningful, service
