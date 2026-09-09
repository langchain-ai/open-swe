import importlib
from types import SimpleNamespace
from typing import Any

import pytest

service_tool = importlib.import_module("agent.tools.create_sandbox_service_url")


class _AsyncClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict[str, Any]]] = []
        self.closed = False

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed = True

    async def service(self, name: str, port: int, **kwargs: Any) -> Any:
        self.calls.append((name, port, kwargs))
        return SimpleNamespace(
            browser_url="https://service.example/auth?token=secret",
            service_url="https://service.example",
            token="secret",
            expires_at="2026-08-27T15:00:00Z",
        )


class _Backend:
    id = "sandbox-1"


def _configure(
    monkeypatch: pytest.MonkeyPatch, *, static_ui: bool = False
) -> tuple[_Backend, _AsyncClient]:
    monkeypatch.setattr(
        "agent.run_config.get_config", lambda: {"configurable": {"thread_id": "thread-1"}}
    )
    backend = _Backend()

    async def get_backend(_thread_id: str) -> _Backend:
        return backend

    client = _AsyncClient()
    monkeypatch.setattr(service_tool, "get_sandbox_backend", get_backend)
    monkeypatch.setattr(service_tool, "unwrap_sandbox_backend", lambda value: value)
    monkeypatch.setattr(service_tool, "get_async_sandbox_client", lambda: client)
    monkeypatch.setattr(service_tool, "serves_static_ui", lambda: static_ui)
    monkeypatch.setattr(service_tool, "dashboard_base_url", lambda: "https://dash.example")
    return backend, client


async def test_create_sandbox_service_url_proxies_through_the_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client = _configure(monkeypatch)

    result = await service_tool.create_sandbox_service_url(3000)

    assert result == {
        "url": "https://dash.example/sandbox/thread-1/3000/",
        "port": 3000,
        "base_path": "/sandbox/thread-1/3000/",
    }
    # The dashboard mints the credential per request; the agent never holds one.
    assert client.calls == []


async def test_create_sandbox_service_url_falls_back_without_a_dashboard_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client = _configure(monkeypatch, static_ui=True)

    result = await service_tool.create_sandbox_service_url(3000)

    assert result == {
        "url": "https://service.example/auth?token=secret",
        "port": 3000,
        "expires_at": "2026-08-27T15:00:00Z",
    }
    assert client.calls == [("sandbox-1", 3000, {"expires_in_seconds": 86400})]
    assert client.closed is True


@pytest.mark.parametrize("port", [True, 0, 65536, 3.5, "3000"])
async def test_create_sandbox_service_url_rejects_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
    port: Any,
) -> None:
    _, client = _configure(monkeypatch)

    with pytest.raises(ValueError, match="port must be an integer between 1 and 65535"):
        await service_tool.create_sandbox_service_url(port)

    assert client.calls == []


async def test_create_sandbox_service_url_detects_sandbox_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = _configure(monkeypatch, static_ui=True)
    current = [backend, _Backend()]
    monkeypatch.setattr(service_tool, "unwrap_sandbox_backend", lambda _value: current.pop(0))

    with pytest.raises(RuntimeError, match="sandbox changed while creating the service URL"):
        await service_tool.create_sandbox_service_url(3000)
