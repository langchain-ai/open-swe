from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from agent.dashboard import routes
from agent.sandboxes.providers import langsmith


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict[str, Any]]] = []

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def service(self, name: str, port: int, **kwargs: Any) -> Any:
        self.calls.append((name, port, kwargs))
        return SimpleNamespace(
            browser_url="https://svc.example/auth?token=secret",
            service_url="https://svc.example",
            token="secret",
            expires_at="2026-09-09T15:00:00Z",
        )


class _Store:
    """The LangGraph Store, holding what this deployment already minted."""

    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any]:
        value = self.items.get((tuple(namespace), key))
        if value is None:
            raise KeyError(key)
        return {"value": value}

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value


def _in(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    store = _Store()
    monkeypatch.setattr(routes, "langgraph_client", lambda: SimpleNamespace(store=store))
    return store


@pytest.fixture
def langsmith_sandbox(monkeypatch: pytest.MonkeyPatch) -> _Client:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    client = _Client()
    monkeypatch.setattr(langsmith, "get_async_sandbox_client", lambda: client)
    return client


async def test_service_url_returns_the_token_not_the_browser_url(
    monkeypatch: pytest.MonkeyPatch, langsmith_sandbox: _Client, store: _Store
) -> None:
    get_sandbox = AsyncMock(return_value=("sandbox-1", "repo"))
    monkeypatch.setattr(routes, "get_dashboard_terminal_sandbox", get_sandbox)
    response = Response()

    service = await routes.api_thread_service_url(
        "thread-1", 3000, response, {"sub": "alice", "email": "alice@example.com"}
    )

    get_sandbox.assert_awaited_once_with("thread-1", "alice", email="alice@example.com")
    assert service == {
        "service_url": "https://svc.example",
        "token": "secret",
        "expires_at": "2026-09-09T15:00:00Z",
    }
    assert langsmith_sandbox.calls == [("sandbox-1", 3000, {"expires_in_seconds": 3600})]
    assert response.headers["cache-control"] == "no-store"
    assert store.items[(("sandbox_service", "sandbox-1"), "3000")] == service


async def test_service_url_reuses_a_stored_token(
    monkeypatch: pytest.MonkeyPatch, langsmith_sandbox: _Client, store: _Store
) -> None:
    monkeypatch.setattr(
        routes, "get_dashboard_terminal_sandbox", AsyncMock(return_value=("sandbox-1", None))
    )
    stored = {
        "service_url": "https://svc.example",
        "token": "already-minted",
        "expires_at": _in(1800),
    }
    store.items[(("sandbox_service", "sandbox-1"), "3000")] = stored

    service = await routes.api_thread_service_url("thread-1", 3000, Response(), {"sub": "alice"})

    assert service == stored
    assert langsmith_sandbox.calls == []


async def test_service_url_replaces_an_expiring_token(
    monkeypatch: pytest.MonkeyPatch, langsmith_sandbox: _Client, store: _Store
) -> None:
    monkeypatch.setattr(
        routes, "get_dashboard_terminal_sandbox", AsyncMock(return_value=("sandbox-1", None))
    )
    key = (("sandbox_service", "sandbox-1"), "3000")
    store.items[key] = {
        "service_url": "https://svc.example",
        "token": "about-to-expire",
        "expires_at": _in(30),
    }

    service = await routes.api_thread_service_url("thread-1", 3000, Response(), {"sub": "alice"})

    assert service["token"] == "secret"
    assert store.items[key]["token"] == "secret"
    assert len(langsmith_sandbox.calls) == 1


@pytest.mark.parametrize("port", [0, 65536])
async def test_service_url_rejects_ports_outside_the_range(
    monkeypatch: pytest.MonkeyPatch, langsmith_sandbox: _Client, port: int
) -> None:
    get_sandbox = AsyncMock(return_value=("sandbox-1", None))
    monkeypatch.setattr(routes, "get_dashboard_terminal_sandbox", get_sandbox)

    with pytest.raises(HTTPException) as exc_info:
        await routes.api_thread_service_url("thread-1", port, Response(), {"sub": "alice"})

    assert exc_info.value.status_code == 422
    get_sandbox.assert_not_awaited()


async def test_service_url_requires_a_langsmith_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "local")

    with pytest.raises(HTTPException) as exc_info:
        await routes.api_thread_service_url("thread-1", 3000, Response(), {"sub": "alice"})

    assert exc_info.value.status_code == 400
