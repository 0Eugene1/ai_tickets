from __future__ import annotations

import asyncio

import httpx

from app.config import Settings


class TelegramError(RuntimeError):
    """Delivery error that never includes the bot token or request URL."""


class TelegramNotifier:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.enabled = settings.telegram_enabled
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self._transport = transport
        if self.enabled and (not self.token or not self.chat_id):
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required when Telegram is enabled")

    async def send(self, markdown: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
            for part in self._chunks(markdown):
                for attempt in range(3):
                    try:
                        response = await client.post(
                            url,
                            json={
                                "chat_id": self.chat_id,
                                "text": part,
                                "disable_web_page_preview": True,
                            },
                        )
                        if response.status_code == 400:
                            try:
                                migrated_chat_id = response.json().get("parameters", {}).get(
                                    "migrate_to_chat_id"
                                )
                            except ValueError:
                                migrated_chat_id = None
                            if migrated_chat_id:
                                self.chat_id = str(migrated_chat_id)
                                continue
                        if response.status_code < 400:
                            break
                        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                            raise TelegramError(
                                f"Telegram rejected the message (HTTP {response.status_code})"
                            ) from None
                    except (httpx.TimeoutException, httpx.NetworkError) as exc:
                        if attempt == 2:
                            raise TelegramError(
                                f"Telegram delivery failed: {type(exc).__name__}"
                            ) from None
                    await asyncio.sleep(2**attempt)

    @staticmethod
    def _chunks(text: str, limit: int = 3900) -> list[str]:
        chunks: list[str] = []
        current = ""
        for paragraph in text.split("\n\n"):
            pieces = [paragraph[index:index + limit] for index in range(0, len(paragraph), limit)] or [""]
            for piece in pieces:
                candidate = f"{current}\n\n{piece}" if current else piece
                if len(candidate) <= limit:
                    current = candidate
                else:
                    if current:
                        chunks.append(current)
                    current = piece
        if current:
            chunks.append(current)
        return chunks
