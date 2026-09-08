import base64
import json

import pytest

from agent.resources import stagehand_runtime
from agent.tool_loaders import stagehand_browser


class Result:
    exit_code = 0

    def __init__(self, output: str) -> None:
        self.output = output


class Backend:
    command = ""

    async def aexecute(self, command: str, *, timeout: int | None = None) -> Result:
        self.command = command
        return Result('{"success":true,"url":"http://localhost:3000"}')


@pytest.mark.asyncio
async def test_browser_navigate_runs_in_thread_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = Backend()

    async def get_backend(_thread_id: str) -> Backend:
        return backend

    monkeypatch.setattr(stagehand_browser, "get_sandbox_backend", get_backend)
    monkeypatch.setattr(stagehand_browser, "_thread_id", lambda: "thread-1")

    result = await stagehand_browser.browser_navigate("http://localhost:3000")

    encoded = backend.command.rsplit(" ", 1)[-1]
    request = json.loads(base64.urlsafe_b64decode(encoded).decode())
    assert result["success"] is True
    assert request["url"] == "http://localhost:3000"
    assert "eyJvcGVyYXRpb24iOiJoZWFsdGgifQ==" in backend.command
    assert "rm -f /tmp/open-swe-stagehand.sock" in backend.command
    assert "setsid python /opt/open-swe/stagehand_runtime.py serve" in backend.command


@pytest.mark.parametrize("ip", ["100.64.0.1", "127.0.0.1"])
def test_stagehand_resolve_accepts_loopback_and_shared_addresses(
    monkeypatch: pytest.MonkeyPatch, ip: str
) -> None:
    monkeypatch.setattr(
        stagehand_runtime.socket,
        "getaddrinfo",
        lambda host, port: [(None, None, None, None, (ip, 0))],
    )
    assert stagehand_runtime._resolve("http://example.test/")[0] is True


@pytest.mark.parametrize("ip", ["10.0.0.1", "169.254.1.1", "192.168.1.1"])
def test_stagehand_resolve_rejects_internal_addresses(
    monkeypatch: pytest.MonkeyPatch, ip: str
) -> None:
    monkeypatch.setattr(
        stagehand_runtime.socket,
        "getaddrinfo",
        lambda host, port: [(None, None, None, None, (ip, 0))],
    )
    safe, reason, _ = stagehand_runtime._resolve("http://example.test/")
    assert safe is False
    assert reason == f"URL resolves to blocked address: {ip}"


@pytest.mark.asyncio
async def test_stagehand_session_keeps_proxy_and_bypasses_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_options: dict[str, object] = {}

    class Sessions:
        async def start(self, *, browser: dict[str, object], model_name: str) -> object:
            launch_options.update(browser["launch_options"])
            return object()

    class Client:
        sessions = Sessions()

    async def fake_proxy() -> int:
        return 43123

    monkeypatch.setattr(stagehand_runtime, "_SESSION", None)
    monkeypatch.setattr(stagehand_runtime, "_CLIENT", None)
    monkeypatch.setattr(stagehand_runtime, "_proxy", fake_proxy)
    monkeypatch.setattr(stagehand_runtime, "AsyncStagehand", lambda **kwargs: Client())

    await stagehand_runtime._session({"model_name": "test", "headless": True})

    assert launch_options["args"] == [
        "--proxy-server=http://127.0.0.1:43123",
        "--proxy-bypass-list=<-loopback>",
    ]


def test_browser_tools_require_secure_supported_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.delenv("STAGEHAND_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("STAGEHAND_MODEL", "anthropic/claude-sonnet-4-5")
    assert stagehand_browser.browser_tools_enabled() is True

    tools = stagehand_browser.load_browser_tools()
    assert [tool.name for tool in tools] == [
        "browser_navigate",
        "browser_act",
        "browser_observe",
        "browser_extract",
        "browser_close",
    ]

    monkeypatch.setenv("SANDBOX_TYPE", "local")
    assert stagehand_browser.browser_tools_enabled() is False
    assert stagehand_browser.load_browser_tools() == []
