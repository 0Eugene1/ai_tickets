from __future__ import annotations

from typing import Any
from urllib.parse import quote, urljoin

import httpx

from app.integrations.confluence import ConfluenceClient, ConfluenceError


class ConfluencePublisher(ConfluenceClient):
    """Explicit write client used only by the manual publishing command."""

    def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        url = self._initial_url(endpoint)
        try:
            response = self._client.request(method, url, params=params, json=payload)
        except httpx.HTTPError as exc:
            raise ConfluenceError(
                f"Confluence publish request failed: {type(exc).__name__}"
            ) from exc
        if allow_not_found and response.status_code == 404:
            return None
        if response.is_redirect:
            raise ConfluenceError("Confluence redirected the publish request")
        if response.status_code in {401, 403}:
            raise ConfluenceError(
                f"Confluence denied write access (HTTP {response.status_code}); "
                "check token and space permissions"
            )
        if response.status_code == 409:
            raise ConfluenceError("Confluence rejected the update because of a version conflict")
        try:
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ConfluenceError(
                f"Confluence publish returned HTTP {response.status_code}"
            ) from exc
        if not isinstance(result, dict):
            raise ConfluenceError("Confluence publish returned an unexpected response")
        return result

    def get_space(self, key: str) -> dict[str, Any] | None:
        return self._request_json(
            "GET",
            f"rest/api/space/{quote(key, safe='')}",
            params={"expand": "homepage"},
            allow_not_found=True,
        )

    def ensure_space(self, key: str, name: str, description: str) -> tuple[dict[str, Any], bool]:
        existing = self.get_space(key)
        if existing:
            return existing, False
        created = self._request_json(
            "POST",
            "rest/api/space",
            payload={
                "key": key,
                "name": name,
                "description": {
                    "plain": {"value": description, "representation": "plain"}
                },
            },
        )
        if not created:
            raise ConfluenceError("Confluence did not return the created space")
        return created, True

    def find_page(self, space_key: str, title: str) -> dict[str, Any] | None:
        result = self._request_json(
            "GET",
            "rest/api/content",
            params={
                "type": "page",
                "status": "current",
                "spaceKey": space_key,
                "title": title,
                "expand": "version",
                "limit": 10,
            },
        )
        rows = result.get("results", []) if result else []
        return next((row for row in rows if row.get("title") == title), None)

    def get_page(self, page_id: str) -> dict[str, Any]:
        result = self._request_json(
            "GET",
            f"rest/api/content/{quote(page_id, safe='')}",
            params={"expand": "version,space,ancestors"},
        )
        if not result:
            raise ConfluenceError(f"Confluence page {page_id} was not found")
        return result

    def upsert_page(
        self,
        *,
        space_key: str,
        title: str,
        storage_body: str,
        ancestor_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        existing = self.find_page(space_key, title)
        body = {"storage": {"value": storage_body, "representation": "storage"}}
        if not existing:
            payload: dict[str, Any] = {
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": body,
            }
            if ancestor_id:
                payload["ancestors"] = [{"id": ancestor_id}]
            created = self._request_json("POST", "rest/api/content", payload=payload)
            if not created:
                raise ConfluenceError("Confluence did not return the created page")
            return created, True

        page_id = str(existing["id"])
        version = int((existing.get("version") or {}).get("number", 1)) + 1
        updated = self._request_json(
            "PUT",
            f"rest/api/content/{quote(page_id, safe='')}",
            payload={
                "id": page_id,
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "version": {"number": version},
                "body": body,
            },
        )
        if not updated:
            raise ConfluenceError("Confluence did not return the updated page")
        return updated, False

    def update_page(self, page_id: str, storage_body: str) -> dict[str, Any]:
        existing = self.get_page(page_id)
        title = str(existing.get("title", ""))
        version = int((existing.get("version") or {}).get("number", 1)) + 1
        updated = self._request_json(
            "PUT",
            f"rest/api/content/{quote(page_id, safe='')}",
            payload={
                "id": page_id,
                "type": "page",
                "title": title,
                "version": {"number": version},
                "body": {
                    "storage": {"value": storage_body, "representation": "storage"}
                },
            },
        )
        if not updated:
            raise ConfluenceError("Confluence did not return the updated homepage")
        return updated

    def web_url(self, item: dict[str, Any]) -> str:
        links = item.get("_links") if isinstance(item.get("_links"), dict) else {}
        webui = str(links.get("webui", ""))
        if webui:
            return urljoin(self.base_url + "/", webui.lstrip("/"))
        page_id = str(item.get("id", ""))
        return f"{self.base_url}/pages/viewpage.action?pageId={page_id}"
