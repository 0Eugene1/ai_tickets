from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit

import httpx


class ConfluenceError(RuntimeError):
    """A safe error raised by the read-only Confluence client."""


@dataclass(frozen=True)
class ConfluenceConfig:
    base_url: str
    auth_mode: str
    api_token: str
    email: str | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ConfluenceBatch:
    results: list[dict[str, Any]]
    next_url: str | None


class ConfluenceClient:
    """Minimal read-only client compatible with Confluence Cloud and Data Center."""

    def __init__(
        self,
        config: ConfluenceConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("CONFLUENCE_BASE_URL must be an absolute URL")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("CONFLUENCE_BASE_URL must use HTTPS")
        if config.auth_mode not in {"basic", "bearer"}:
            raise ValueError("CONFLUENCE_AUTH_MODE must be 'basic' or 'bearer'")
        if not config.api_token:
            raise ValueError("CONFLUENCE_API_TOKEN is required")
        if config.auth_mode == "basic" and not config.email:
            raise ValueError("CONFLUENCE_EMAIL is required for basic auth")

        auth: httpx.Auth | None = None
        headers = {"Accept": "application/json", "User-Agent": "ticket-assistant/0.1"}
        if config.auth_mode == "basic":
            auth = httpx.BasicAuth(config.email or "", config.api_token)
        else:
            headers["Authorization"] = f"Bearer {config.api_token}"

        self._client = httpx.Client(
            auth=auth,
            headers=headers,
            timeout=config.timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ConfluenceClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _initial_url(self, endpoint: str) -> str:
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    def _safe_next_url(self, next_link: str | None) -> str | None:
        if not next_link:
            return None
        base = urlsplit(self.base_url)
        candidate = urlsplit(next_link)
        if candidate.scheme or candidate.netloc:
            if (candidate.scheme, candidate.netloc) != (base.scheme, base.netloc):
                raise ConfluenceError("Confluence returned a cross-origin pagination URL")
            return next_link

        base_path = base.path.rstrip("/")
        if next_link.startswith(base_path + "/"):
            return f"{base.scheme}://{base.netloc}{next_link}"
        if next_link.startswith("/rest/") and base_path:
            return f"{self.base_url}{next_link}"
        return urljoin(self.base_url + "/", next_link)

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ConfluenceError(f"Confluence request failed: {type(exc).__name__}") from exc
        if response.is_redirect:
            raise ConfluenceError(
                "Confluence redirected the API request; check the base URL and authentication mode"
            )
        if response.status_code in {401, 403}:
            raise ConfluenceError(
                f"Confluence denied read access (HTTP {response.status_code}); check token and permissions"
            )
        if response.status_code == 429:
            raise ConfluenceError("Confluence rate limit reached (HTTP 429); retry later")
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ConfluenceError(f"Confluence returned HTTP {response.status_code}") from exc
        if not isinstance(payload, dict):
            raise ConfluenceError("Confluence returned an unexpected response")
        return payload

    def get_batch(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> ConfluenceBatch:
        payload = self._get(url, params)
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise ConfluenceError("Confluence response has no result list")
        links = payload.get("_links") if isinstance(payload.get("_links"), dict) else {}
        return ConfluenceBatch(
            results=[item for item in results if isinstance(item, dict)],
            next_url=self._safe_next_url(links.get("next")),
        )

    def iter_spaces(self, *, limit: int = 50) -> Iterator[dict[str, Any]]:
        url = self._initial_url("rest/api/space")
        params: dict[str, Any] | None = {"limit": max(1, min(limit, 100))}
        while url:
            batch = self.get_batch(url, params)
            yield from batch.results
            url, params = batch.next_url, None

    def page_batch(
        self,
        *,
        space_key: str,
        limit: int = 50,
        request_url: str | None = None,
    ) -> ConfluenceBatch:
        if request_url:
            return self.get_batch(request_url)
        return self.get_batch(
            self._initial_url("rest/api/content"),
            {
                "type": "page",
                "status": "current",
                "spaceKey": space_key,
                "expand": "body.storage,version,space,ancestors,metadata.labels",
                "limit": max(1, min(limit, 50)),
            },
        )
