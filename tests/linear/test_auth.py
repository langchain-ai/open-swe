"""Authorization header selection for Linear requests."""

from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from agent.linear import auth as linear_auth


class _RecordingHandler:
    def __init__(self, *, unauthorized_calls: int = 0) -> None:
        self.authorizations: list[str] = []
        self.token_requests: list[str] = []
        self._remaining_unauthorized = unauthorized_calls
        self._minted = 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/oauth/token":
            self.token_requests.append(request.content.decode())
            self._minted += 1
            return httpx2.Response(200, json={"access_token": f"token-{self._minted}"})
        self.authorizations.append(request.headers.get("Authorization", ""))
        if self._remaining_unauthorized > 0:
            self._remaining_unauthorized -= 1
            return httpx2.Response(401, json={"errors": [{"message": "unauthorized"}]})
        return httpx2.Response(200, json={"data": {"viewer": {"id": "app-1"}}})


@pytest.fixture(autouse=True)
def _reset_provider() -> Iterator[None]:
    linear_auth._provider = None
    linear_auth._provider_credentials = None
    yield
    linear_auth._provider = None
    linear_auth._provider_credentials = None


def _route_all_traffic(monkeypatch: pytest.MonkeyPatch, handler: _RecordingHandler) -> None:
    """Serve every httpx2 client that does not bring its own transport from ``handler``."""
    real_client = httpx2.AsyncClient

    class _MockedAsyncClient(real_client):
        def __init__(self, **kwargs: Any) -> None:
            kwargs.setdefault("transport", httpx2.MockTransport(handler))
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", _MockedAsyncClient)


async def _graphql_call(handler: _RecordingHandler) -> httpx2.Response:
    async with httpx2.AsyncClient(
        auth=linear_auth.LinearAuth(), transport=httpx2.MockTransport(handler)
    ) as client:
        return await client.post("https://api.linear.app/graphql", json={"query": "{ viewer }"})


async def test_api_key_is_sent_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_key")
    monkeypatch.delenv("LINEAR_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("LINEAR_OAUTH_CLIENT_SECRET", raising=False)
    handler = _RecordingHandler()

    response = await _graphql_call(handler)

    assert response.status_code == 200
    assert handler.authorizations == ["lin_api_key"]
    assert handler.token_requests == []
    assert linear_auth.linear_app_mode() is False
    assert linear_auth.linear_configured() is True


async def test_app_credentials_mint_an_app_actor_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_OAUTH_CLIENT_ID", "client-1")
    monkeypatch.setenv("LINEAR_OAUTH_CLIENT_SECRET", "secret-1")
    handler = _RecordingHandler()
    _route_all_traffic(monkeypatch, handler)

    response = await _graphql_call(handler)

    assert response.status_code == 200
    assert handler.authorizations == ["Bearer token-1"]
    assert len(handler.token_requests) == 1
    assert "grant_type=client_credentials" in handler.token_requests[0]
    assert "actor=app" in handler.token_requests[0]
    assert linear_auth.linear_app_mode() is True


async def test_a_401_remints_the_token_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_OAUTH_CLIENT_ID", "client-2")
    monkeypatch.setenv("LINEAR_OAUTH_CLIENT_SECRET", "secret-2")
    handler = _RecordingHandler(unauthorized_calls=1)
    _route_all_traffic(monkeypatch, handler)

    response = await _graphql_call(handler)

    assert response.status_code == 200
    assert handler.authorizations == ["Bearer token-1", "Bearer token-2"]
    assert len(handler.token_requests) == 2


async def test_a_rejected_client_credential_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_OAUTH_CLIENT_ID", "client-3")
    monkeypatch.setenv("LINEAR_OAUTH_CLIENT_SECRET", "wrong-secret")

    def reject(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": "invalid_client"})

    real_client = httpx2.AsyncClient

    class _RejectingClient(real_client):
        def __init__(self, **kwargs: Any) -> None:
            kwargs.setdefault("transport", httpx2.MockTransport(reject))
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", _RejectingClient)
    provider = linear_auth.app_token_provider()

    assert provider is not None
    with pytest.raises(linear_auth.LinearAuthError):
        await provider.get_token()
