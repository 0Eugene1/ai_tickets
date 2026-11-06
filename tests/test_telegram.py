import asyncio
import json

import httpx

from app.config import Settings
from app.integrations.telegram import TelegramError, TelegramNotifier


def test_long_report_is_split_within_telegram_limit() -> None:
    text = ("Первый раздел " * 250) + "\n\n" + ("Второй раздел " * 250)
    chunks = TelegramNotifier._chunks(text, limit=500)

    assert "".join(chunks).replace("\n\n", "") == text.replace("\n\n", "")
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_migrated_supergroup_chat_id_is_used_automatically() -> None:
    chat_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chat_id = str(json.loads(request.content)["chat_id"])
        chat_ids.append(chat_id)
        if chat_id == "-123":
            return httpx.Response(
                400,
                json={
                    "ok": False,
                    "description": "group chat was upgraded",
                    "parameters": {"migrate_to_chat_id": -100999},
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    settings = Settings(
        telegram_enabled=True,
        telegram_bot_token="test-secret-token",
        telegram_chat_id="-123",
        metabase_polling_enabled=False,
    )
    notifier = TelegramNotifier(settings, transport=httpx.MockTransport(handler))

    asyncio.run(notifier.send("Проверка"))

    assert chat_ids == ["-123", "-100999"]
    assert notifier.chat_id == "-100999"
