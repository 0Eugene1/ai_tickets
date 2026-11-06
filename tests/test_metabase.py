import json

import httpx
import pytest

from app.integrations.metabase import MetabaseClient, MetabaseConfig


def test_rejects_remote_unencrypted_http_by_default() -> None:
    with pytest.raises(ValueError, match="unencrypted HTTP"):
        MetabaseClient(
            MetabaseConfig(base_url="http://metabase.internal", api_token="secret")
        )


def test_uses_api_key_without_exposing_it_in_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "secret"
        return httpx.Response(200, json={"id": 7, "common_name": "Operator"})

    with MetabaseClient(
        MetabaseConfig(base_url="https://metabase.internal", api_token="secret"),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.current_user()

    assert result == {"id": 7, "common_name": "Operator"}
    assert "secret" not in json.dumps(result)


def test_runs_saved_question_as_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/card/42/query/json"
        assert json.loads(request.content) == {"parameters": []}
        return httpx.Response(200, json=[{"ticket_id": 1}])

    with MetabaseClient(
        MetabaseConfig(base_url="https://metabase.internal", api_token="secret"),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.run_card(42)

    assert result == [{"ticket_id": 1}]


def test_native_discovery_allows_only_single_select() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/api/dataset/json"
        assert payload["query"]["database"] == 5
        assert payload["query"]["native"]["query"] == "SELECT COUNT(*) AS total FROM tickets"
        assert payload["format_rows"] is False
        return httpx.Response(200, json=[{"TOTAL": 10}])

    with MetabaseClient(
        MetabaseConfig(base_url="https://metabase.internal", api_token="secret"),
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.run_native_select(5, "SELECT COUNT(*) AS total FROM tickets") == [
            {"TOTAL": 10}
        ]
        with pytest.raises(ValueError, match="forbidden"):
            client.run_native_select(5, "SELECT * FROM tickets; DELETE FROM tickets")
