import importlib
from typing import Any

import pytest

service_tool = importlib.import_module("agent.tools.expose_port")


class _Backend:
    id = "sandbox-1"


def _configure(monkeypatch: pytest.MonkeyPatch) -> tuple[_Backend, list[tuple[str, int]]]:
    monkeypatch.setattr(
        "agent.run_config.get_config", lambda: {"configurable": {"thread_id": "thread-1"}}
    )
    backend = _Backend()
    calls: list[tuple[str, int]] = []

    async def get_backend(_thread_id: str) -> _Backend:
        return backend

    async def create_url(sandbox_id: str, port: int) -> str:
        calls.append((sandbox_id, port))
        return "https://l-abc123.sandbox.example/"

    monkeypatch.setattr(service_tool, "get_sandbox_backend", get_backend)
    monkeypatch.setattr(service_tool, "unwrap_sandbox_backend", lambda value: value)
    monkeypatch.setattr(service_tool, "create_workspace_service_url", create_url)
    return backend, calls


async def test_expose_port_shares_the_port_with_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, calls = _configure(monkeypatch)

    result = await service_tool.expose_port(3000)

    assert result == {"url": "https://l-abc123.sandbox.example/", "port": 3000}
    assert calls == [("sandbox-1", 3000)]


@pytest.mark.parametrize("port", [True, 0, 65536, 3.5, "3000"])
async def test_expose_port_rejects_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
    port: Any,
) -> None:
    _, calls = _configure(monkeypatch)

    with pytest.raises(ValueError, match="port must be an integer between 1 and 65535"):
        await service_tool.expose_port(port)

    assert calls == []


async def test_expose_port_detects_sandbox_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = _configure(monkeypatch)
    current = [backend, _Backend()]
    monkeypatch.setattr(service_tool, "unwrap_sandbox_backend", lambda _value: current.pop(0))

    with pytest.raises(RuntimeError, match="sandbox changed while creating the service URL"):
        await service_tool.expose_port(3000)
