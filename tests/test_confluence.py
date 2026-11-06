from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.integrations.confluence import ConfluenceClient, ConfluenceConfig, ConfluenceError
from app.knowledge.confluence_loader import _jsonl_line, export_pages, storage_to_text


def _page(page_id: str, version: int, title: str = "Mobile app") -> dict:
    return {
        "id": page_id,
        "title": title,
        "space": {"key": "MPLK", "name": "Mobile"},
        "version": {"number": version, "when": "2026-09-08T00:00:00Z"},
        "ancestors": [{"id": "1"}],
        "metadata": {"labels": {"results": [{"name": "support"}]}},
        "body": {"storage": {"value": "<h1>Problem</h1><p>Check service &amp; tariff.</p>"}},
        "_links": {"webui": f"/display/MPLK/{page_id}"},
    }


def test_storage_to_text_keeps_structure_and_skips_scripts() -> None:
    value = "<h2>Title</h2><p>First&nbsp;line<br>second</p><script>secret()</script>"
    assert storage_to_text(value) == "Title\nFirst line\nsecond"


def test_jsonl_escapes_unicode_line_separators() -> None:
    line = _jsonl_line({"text": "before\u2028after\u2029end"})
    assert line.count("\n") == 1
    assert "\\u2028" in line
    assert "\\u2029" in line
    assert json.loads(line)["text"] == "before\u2028after\u2029end"


def test_cloud_pagination_keeps_auth_on_same_origin_only() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={"results": [_page("10", 1)], "_links": {"next": "/wiki/rest/api/content?start=1"}},
            )
        return httpx.Response(200, json={"results": [_page("11", 1)], "_links": {}})

    client = ConfluenceClient(
        ConfluenceConfig(
            base_url="https://example.atlassian.net/wiki",
            auth_mode="basic",
            email="reader@example.test",
            api_token="test-token",
        ),
        transport=httpx.MockTransport(handler),
    )
    try:
        first = client.page_batch(space_key="MPLK")
        second = client.page_batch(space_key="MPLK", request_url=first.next_url)
    finally:
        client.close()

    assert len(first.results) == len(second.results) == 1
    assert requests[1].url == httpx.URL("https://example.atlassian.net/wiki/rest/api/content?start=1")
    assert requests[0].headers["authorization"].startswith("Basic ")


def test_rejects_cross_origin_pagination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [], "_links": {"next": "https://attacker.test/steal"}},
        )

    client = ConfluenceClient(
        ConfluenceConfig(
            base_url="https://example.atlassian.net/wiki",
            auth_mode="bearer",
            api_token="test-token",
        ),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ConfluenceError, match="cross-origin"):
            client.page_batch(space_key="MPLK")
    finally:
        client.close()


class _FakeClient:
    base_url = "https://example.atlassian.net/wiki"

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def page_batch(self, *, space_key: str, limit: int, request_url: str | None = None):
        from app.integrations.confluence import ConfluenceBatch

        self.calls.append(request_url)
        if request_url is None:
            return ConfluenceBatch([_page("10", 1)], "https://example.atlassian.net/wiki/rest/api/content?start=1")
        return ConfluenceBatch([_page("11", 2)], None)


class _FailingClient(_FakeClient):
    def page_batch(self, *, space_key: str, limit: int, request_url: str | None = None):
        if request_url is not None:
            raise RuntimeError("temporary failure")
        return super().page_batch(space_key=space_key, limit=limit, request_url=request_url)


class _ResumeClient(_FakeClient):
    def page_batch(self, *, space_key: str, limit: int, request_url: str | None = None):
        from app.integrations.confluence import ConfluenceBatch

        self.calls.append(request_url)
        assert request_url == "https://example.atlassian.net/wiki/rest/api/content?start=1"
        return ConfluenceBatch([_page("11", 2)], None)


def test_export_is_checkpointed_and_unverified(tmp_path: Path) -> None:
    first = export_pages(_FakeClient(), output_dir=tmp_path, space_keys=["MPLK"])
    second = export_pages(_FakeClient(), output_dir=tmp_path, space_keys=["MPLK"])
    records = [json.loads(line) for line in (tmp_path / "pages.jsonl").read_text(encoding="utf-8").splitlines()]

    assert first["status"] == second["status"] == "completed"
    assert len(records) == 2
    assert all(item["confirmed"] is False for item in records)
    assert all(item["automatic_answer_allowed"] is False for item in records)
    assert records[0]["text"] == "Problem\nCheck service & tariff."
    assert second["pages_skipped"] == 2


def test_export_resumes_from_saved_next_page(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="temporary"):
        export_pages(_FailingClient(), output_dir=tmp_path, space_keys=["MPLK"])

    checkpoint = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "in_progress"
    assert checkpoint["next_request_url"].endswith("start=1")

    resumed = export_pages(_ResumeClient(), output_dir=tmp_path, space_keys=["MPLK"])
    records = (tmp_path / "pages.jsonl").read_text(encoding="utf-8").splitlines()
    assert resumed["status"] == "completed"
    assert len(records) == 2
