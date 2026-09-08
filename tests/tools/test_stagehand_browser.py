import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


def test_stagehand_runtime_imports_without_repository_root(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runtime_path = repo_root / "agent" / "resources" / "stagehand_runtime.py"
    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if Path(entry or ".").resolve() != repo_root],
    )
    for module_name in list(sys.modules):
        if module_name == "agent" or module_name.startswith("agent."):
            monkeypatch.delitem(sys.modules, module_name)

    spec = importlib.util.spec_from_file_location("stagehand_runtime", runtime_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


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
