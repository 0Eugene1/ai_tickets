from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx


class MetabaseError(RuntimeError):
    """Safe integration error that never includes credentials or response data."""


@dataclass(frozen=True)
class MetabaseConfig:
    base_url: str
    api_token: str
    auth_mode: Literal["api_key", "session"] = "api_key"
    timeout_seconds: float = 30.0
    allow_insecure_http: bool = False


class MetabaseClient:
    """Minimal client for reading and executing existing saved questions."""

    def __init__(
        self,
        config: MetabaseConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("METABASE_BASE_URL must be an absolute URL")
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("METABASE_BASE_URL must use HTTP or HTTPS")
        local_host = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "http" and not local_host and not config.allow_insecure_http:
            raise ValueError(
                "METABASE_BASE_URL uses unencrypted HTTP; provide HTTPS or explicitly set "
                "METABASE_ALLOW_INSECURE_HTTP=true"
            )
        if config.auth_mode not in {"api_key", "session"}:
            raise ValueError("METABASE_AUTH_MODE must be 'api_key' or 'session'")
        if not config.api_token:
            raise ValueError("METABASE_API_TOKEN is required")

        header_name = "X-API-Key" if config.auth_mode == "api_key" else "X-Metabase-Session"
        self._client = httpx.Client(
            headers={
                "Accept": "application/json",
                "User-Agent": "ticket-assistant/0.1",
                header_name: config.api_token,
            },
            timeout=config.timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MetabaseClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(
                method,
                f"{self.base_url}/{endpoint.lstrip('/')}",
                params=params,
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise MetabaseError(f"Metabase request failed: {type(exc).__name__}") from exc
        if response.is_redirect:
            raise MetabaseError("Metabase redirected the API request; check METABASE_BASE_URL")
        if response.status_code in {401, 403}:
            raise MetabaseError(
                f"Metabase denied API access (HTTP {response.status_code}); check token and permissions"
            )
        if response.status_code == 429:
            raise MetabaseError("Metabase rate limit reached (HTTP 429)")
        try:
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MetabaseError(f"Metabase returned HTTP {response.status_code}") from exc

    def current_user(self) -> dict[str, Any]:
        result = self._request("GET", "api/user/current")
        if not isinstance(result, dict):
            raise MetabaseError("Metabase returned an unexpected current-user response")
        return result

    def search(self, query: str) -> list[dict[str, Any]]:
        result = self._request("GET", "api/search", params={"q": query})
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise MetabaseError("Metabase returned an unexpected search response")
        return [item for item in result["data"] if isinstance(item, dict)]

    def get_card(self, card_id: int) -> dict[str, Any]:
        result = self._request("GET", f"api/card/{card_id}")
        if not isinstance(result, dict):
            raise MetabaseError("Metabase returned an unexpected saved-question response")
        return result

    def run_card(
        self,
        card_id: int,
        *,
        parameters: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        result = self._request(
            "POST",
            f"api/card/{card_id}/query/json",
            payload={"parameters": parameters or []},
        )
        if not isinstance(result, list):
            raise MetabaseError("Metabase returned an unexpected query result")
        return [item for item in result if isinstance(item, dict)]

    def run_native_select(self, database_id: int, query: str) -> list[dict[str, Any]]:
        normalized = query.strip()
        if not re.match(r"(?is)^(?:select|with)\b", normalized):
            raise ValueError("Only SELECT/CTE Metabase discovery queries are allowed")
        if ";" in normalized or re.search(
            r"(?i)\b(?:insert|update|delete|merge|drop|alter|truncate|grant|revoke|create)\b",
            normalized,
        ):
            raise ValueError("Mutating or multi-statement Metabase queries are forbidden")
        result = self._request(
            "POST",
            "api/dataset/json",
            payload={
                "query": {
                    "database": database_id,
                    "type": "native",
                    "native": {"query": normalized, "template-tags": {}},
                    "parameters": [],
                },
                "visualization_settings": {},
                "format_rows": False,
                "pivot_results": False,
            },
        )
        if not isinstance(result, list):
            raise MetabaseError("Metabase returned an unexpected native-query result")
        return [item for item in result if isinstance(item, dict)]
