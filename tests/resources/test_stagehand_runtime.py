import socket

import pytest

from agent.integrations import stagehand_browser
from agent.resources import stagehand_runtime


def _addr_info(ip: str) -> tuple[int, int, int, str, tuple[str, int]]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return family, 0, 0, "", (ip, 0)


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("100.64.0.2", True),
        ("127.0.0.1", True),
        ("93.184.216.34", True),
        ("169.254.169.254", False),
        ("10.0.0.2", False),
    ],
)
def test_resolve_network_policy(monkeypatch: pytest.MonkeyPatch, ip: str, expected: bool) -> None:
    monkeypatch.setattr(stagehand_runtime.socket, "getaddrinfo", lambda *args: [_addr_info(ip)])
    assert stagehand_runtime._resolve("https://example.com")[0] is expected


@pytest.mark.asyncio
async def test_handle_reports_unavailable_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_proxy(_request: dict[str, object]) -> object:
        raise RuntimeError("no reachable proxy endpoint")

    monkeypatch.setattr(stagehand_runtime, "_session", no_proxy)
    result = await stagehand_runtime._handle({"operation": "navigate", "url": "http://localhost"})
    assert result == {
        "success": False,
        "error": "browser automation is unavailable in this sandbox: no reachable proxy endpoint",
    }


@pytest.mark.asyncio
async def test_load_browser_tools_returns_none_when_health_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.setenv("STAGEHAND_MODEL_API_KEY", "secret")
    monkeypatch.setenv("STAGEHAND_MODEL", "anthropic/claude-sonnet-4-5")

    async def failed_health(_operation: str, **_payload: object) -> dict[str, object]:
        return {"success": False, "error": "runtime unavailable"}

    monkeypatch.setattr(stagehand_browser, "_request", failed_health)
    assert await stagehand_browser.load_browser_tools() == []
