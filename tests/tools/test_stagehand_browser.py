import base64
import json

import pytest

from agent.integrations import stagehand_browser


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
    assert "setsid python /opt/open-swe/stagehand_runtime.py serve" in backend.command


def test_browser_tools_require_secure_supported_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.setenv("STAGEHAND_MODEL_API_KEY", "secret")
    monkeypatch.setenv("STAGEHAND_MODEL", "anthropic/claude-sonnet-4-5")
    assert stagehand_browser.browser_tools_enabled() is True

    monkeypatch.setenv("SANDBOX_TYPE", "local")
    assert stagehand_browser.browser_tools_enabled() is False
