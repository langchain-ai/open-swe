from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx
import pytest

from agent.utils import linear
from agent.webhooks import common

_REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _reset_linear_auth(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET", "LINEAR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    linear.clear_linear_token_cache()
    yield
    linear.clear_linear_token_cache()


def _mock_http(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        linear.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _REAL_ASYNC_CLIENT(transport=transport),
    )


def _configure_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_CLIENT_ID", "client-id")
    monkeypatch.setenv("LINEAR_CLIENT_SECRET", "client-secret")


async def test_app_mode_mints_bearer_token_and_reuses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_app(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == linear.LINEAR_OAUTH_URL:
            form = parse_qs(request.content.decode())
            assert form == {
                "grant_type": ["client_credentials"],
                "client_id": ["client-id"],
                "client_secret": ["client-secret"],
                "scope": ["read,write"],
            }
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer app-token"
        return httpx.Response(200, json={"data": {"viewer": {"id": "viewer-1"}}})

    _mock_http(monkeypatch, handler)

    assert await linear._graphql_request("query { viewer { id } }") == {
        "viewer": {"id": "viewer-1"}
    }
    assert await linear._graphql_request("query { viewer { id } }") == {
        "viewer": {"id": "viewer-1"}
    }
    assert [str(request.url) for request in requests].count(linear.LINEAR_OAUTH_URL) == 1
    assert [str(request.url) for request in requests].count(linear.LINEAR_API_URL) == 2


@pytest.mark.parametrize("missing", ["LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET"])
async def test_partial_app_credentials_fail_without_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    _configure_app(monkeypatch)
    monkeypatch.delenv(missing)
    monkeypatch.setenv("LINEAR_API_KEY", "legacy-key")
    client = AsyncMock()
    monkeypatch.setattr(linear.httpx, "AsyncClient", client)

    result = await linear._graphql_request("query { viewer { id } }")

    assert result == {
        "error": "LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET must both be set for Linear app mode"
    }
    client.assert_not_called()


async def test_token_exchange_failure_is_safe_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _configure_app(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == linear.LINEAR_OAUTH_URL
        return httpx.Response(
            400,
            text="client-secret leaked-token",
            request=request,
        )

    _mock_http(monkeypatch, handler)

    result = await linear._graphql_request("query { viewer { id } }")

    assert result == {"error": "Failed to obtain Linear app token"}
    combined = json.dumps(result) + caplog.text
    assert "client-secret" not in combined
    assert "leaked-token" not in combined


async def test_malformed_token_response_does_not_expose_access_token(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _configure_app(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == linear.LINEAR_OAUTH_URL
        return httpx.Response(200, json={"access_token": "leaked-token"})

    _mock_http(monkeypatch, handler)

    result = await linear._graphql_request("query { viewer { id } }")

    assert result == {"error": "Failed to obtain Linear app token"}
    assert "leaked-token" not in json.dumps(result) + caplog.text


async def test_near_expiry_token_is_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    now = 100.0
    monkeypatch.setattr(linear, "_monotonic", lambda: now)
    minted = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal minted
        minted += 1
        return httpx.Response(
            200,
            json={"access_token": f"token-{minted}", "expires_in": 61},
        )

    _mock_http(monkeypatch, handler)

    assert (await linear.get_linear_auth()).headers == {"Authorization": "Bearer token-1"}
    now = 102.0
    assert (await linear.get_linear_auth()).headers == {"Authorization": "Bearer token-2"}
    assert minted == 2


async def test_graphql_401_mints_once_and_replays_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    minted = 0
    api_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal minted, api_calls
        if str(request.url) == linear.LINEAR_OAUTH_URL:
            minted += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{minted}", "expires_in": 3600},
            )
        api_calls += 1
        if api_calls == 1:
            assert request.headers["Authorization"] == "Bearer token-1"
            return httpx.Response(401)
        assert request.headers["Authorization"] == "Bearer token-2"
        return httpx.Response(200, json={"data": {"ok": True}})

    _mock_http(monkeypatch, handler)

    assert await linear._graphql_request("mutation { updateThing }") == {"ok": True}
    assert (minted, api_calls) == (2, 2)


async def test_replacement_401_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "legacy-key")
    minted = 0
    api_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal minted, api_calls
        if str(request.url) == linear.LINEAR_OAUTH_URL:
            minted += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{minted}", "expires_in": 3600},
            )
        api_calls += 1
        assert request.headers["Authorization"] != "legacy-key"
        return httpx.Response(401)

    _mock_http(monkeypatch, handler)

    assert await linear._graphql_request("mutation { updateThing }") == {
        "error": "Linear API request failed with status 401"
    }
    assert (minted, api_calls) == (2, 2)


@pytest.mark.parametrize("status_code", [403, 500])
async def test_non_401_http_failures_are_terminal(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    _configure_app(monkeypatch)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == linear.LINEAR_OAUTH_URL:
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(status_code)

    _mock_http(monkeypatch, handler)

    assert await linear._graphql_request("mutation { updateThing }") == {
        "error": f"Linear API request failed with status {status_code}"
    }
    assert calls.count(linear.LINEAR_API_URL) == 1


async def test_graphql_errors_are_terminal_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    api_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        if str(request.url) == linear.LINEAR_OAUTH_URL:
            return httpx.Response(
                200,
                json={"access_token": "secret-app-token", "expires_in": 3600},
            )
        api_calls += 1
        return httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "message": "denied secret-app-token client-secret",
                        "extensions": {"code": "FORBIDDEN"},
                    }
                ]
            },
        )

    _mock_http(monkeypatch, handler)

    result = await linear._graphql_request("mutation { updateThing }")

    assert api_calls == 1
    serialized = json.dumps(result)
    assert "secret-app-token" not in serialized
    assert "client-secret" not in serialized
    assert "FORBIDDEN" in serialized


async def test_transport_failure_is_not_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    api_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        if str(request.url) == linear.LINEAR_OAUTH_URL:
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        api_calls += 1
        raise httpx.ReadError("connection lost", request=request)

    _mock_http(monkeypatch, handler)

    assert await linear._graphql_request("mutation { updateThing }") == {
        "error": "Linear API request failed"
    }
    assert api_calls == 1


async def test_unexpected_transport_failure_is_safe_and_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _configure_app(monkeypatch)
    api_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        if str(request.url) == linear.LINEAR_OAUTH_URL:
            return httpx.Response(
                200,
                json={"access_token": "secret-app-token", "expires_in": 3600},
            )
        api_calls += 1
        raise RuntimeError("secret-app-token client-secret")

    _mock_http(monkeypatch, handler)

    result = await linear._graphql_request("mutation { updateThing }")

    assert result == {"error": "Linear API request failed"}
    assert api_calls == 1
    combined = json.dumps(result) + caplog.text
    assert "secret-app-token" not in combined
    assert "client-secret" not in combined


async def test_legacy_key_is_bare_warned_and_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "legacy-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == linear.LINEAR_API_URL
        assert request.headers["Authorization"] == "legacy-key"
        return httpx.Response(401)

    _mock_http(monkeypatch, handler)

    assert await linear._graphql_request("query { viewer { id } }") == {
        "error": "Linear API request failed with status 401"
    }
    assert len(requests) == 1
    assert "deprecated" in caplog.text


async def test_legacy_key_is_passed_through_unchanged_to_webhook_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", " legacy-key ")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == linear.LINEAR_API_URL
        assert request.headers["Authorization"] == " legacy-key "
        body = json.loads(request.content)
        if "ReactionCreate" in body["query"]:
            return httpx.Response(
                200,
                json={"data": {"reactionCreate": {"success": True}}},
            )
        return httpx.Response(
            200,
            json={"data": {"issue": {"id": "issue-1"}}},
        )

    _mock_http(monkeypatch, handler)

    assert await common.react_to_linear_comment("comment-1") is True
    assert await common.fetch_linear_issue_details("issue-1") == {"id": "issue-1"}
    assert len(requests) == 2
    assert all(str(request.url) != linear.LINEAR_OAUTH_URL for request in requests)


async def test_app_credentials_take_precedence_and_warn_about_ignored_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _configure_app(monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "legacy-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == linear.LINEAR_OAUTH_URL:
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer app-token"
        return httpx.Response(200, json={"data": {"ok": True}})

    _mock_http(monkeypatch, handler)

    assert await linear._graphql_request("query { viewer { id } }") == {"ok": True}
    assert "ignored" in caplog.text
    assert "legacy-key" not in caplog.text


async def test_neither_configuration_names_both_options(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AsyncMock()
    monkeypatch.setattr(linear.httpx, "AsyncClient", client)

    result = await linear._graphql_request("query { viewer { id } }")

    assert "LINEAR_CLIENT_ID" in result["error"]
    assert "LINEAR_CLIENT_SECRET" in result["error"]
    assert "LINEAR_API_KEY" in result["error"]
    client.assert_not_called()


async def test_webhook_helpers_share_app_graphql_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_app(monkeypatch)
    api_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == linear.LINEAR_OAUTH_URL:
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
        api_requests.append(request)
        assert request.headers["Authorization"] == "Bearer app-token"
        body = json.loads(request.content)
        if "ReactionCreate" in body["query"]:
            return httpx.Response(
                200,
                json={"data": {"reactionCreate": {"success": True}}},
            )
        return httpx.Response(
            200,
            json={"data": {"issue": {"id": "issue-1", "title": "Issue"}}},
        )

    _mock_http(monkeypatch, handler)

    assert await common.react_to_linear_comment("comment-1") is True
    assert await common.fetch_linear_issue_details("issue-1") == {
        "id": "issue-1",
        "title": "Issue",
    }
    assert len(api_requests) == 2


async def test_webhook_helpers_preserve_failure_contracts() -> None:
    with patch.object(common, "_graphql_request", AsyncMock(return_value={"error": "failed"})):
        assert await common.react_to_linear_comment("comment-1") is False
        assert await common.fetch_linear_issue_details("issue-1") is None
