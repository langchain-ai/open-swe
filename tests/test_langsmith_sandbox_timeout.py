"""Client-side execution deadline for the LangSmith sandbox backend."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from langsmith.sandbox import CommandTimeoutError, SandboxConnectionError

from agent.integrations.langsmith import TimeoutLangSmithSandbox


class _FakeHandle:
    def __init__(self, *, sleep: float = 0.0, result: Any = None, raises: Exception | None = None):
        self._sleep = sleep
        self._result = result
        self._raises = raises
        self.killed = False

    @property
    def result(self) -> Any:
        if self._sleep:
            time.sleep(self._sleep)
        if self._raises is not None:
            raise self._raises
        return self._result

    def kill(self) -> None:
        self.killed = True


class _FakeSandbox:
    def __init__(self, handle: _FakeHandle, *, run_raises: Exception | None = None):
        self._handle = handle
        self._run_raises = run_raises
        self.run_calls: list[dict[str, Any]] = []

    def run(self, command: str, *, timeout: int, wait: bool) -> _FakeHandle:
        self.run_calls.append({"command": command, "timeout": timeout, "wait": wait})
        if self._run_raises is not None:
            raise self._run_raises
        return self._handle


def _backend(
    handle: _FakeHandle, *, run_raises: Exception | None = None
) -> TimeoutLangSmithSandbox:
    sb = TimeoutLangSmithSandbox.__new__(TimeoutLangSmithSandbox)
    sb._sandbox = _FakeSandbox(handle, run_raises=run_raises)
    sb._default_timeout = 30 * 60
    return sb


@pytest.fixture(autouse=True)
def _no_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_EXECUTE_CLIENT_GRACE_SECONDS", "0")


async def test_aexecute_kills_on_client_timeout() -> None:
    handle = _FakeHandle(sleep=5.0)
    sb = _backend(handle)
    start = time.monotonic()
    resp = await sb.aexecute("sleep 999", timeout=1)
    assert time.monotonic() - start < 3.0
    assert resp.exit_code == 124
    assert "killed" in resp.output
    assert handle.killed
    assert sb._sandbox.run_calls[0]["wait"] is False


async def test_aexecute_success_combines_streams() -> None:
    handle = _FakeHandle(result=SimpleNamespace(stdout="out", stderr="err", exit_code=0))
    sb = _backend(handle)
    resp = await sb.aexecute("echo hi", timeout=5)
    assert resp.exit_code == 0
    assert resp.output == "out\nerr"
    assert not handle.killed


async def test_aexecute_server_timeout_not_killed() -> None:
    handle = _FakeHandle(raises=CommandTimeoutError("server enforced"))
    sb = _backend(handle)
    resp = await sb.aexecute("make hang", timeout=2)
    assert resp.exit_code == 124
    assert "on the sandbox" in resp.output
    assert not handle.killed


def test_execute_kills_on_client_timeout() -> None:
    handle = _FakeHandle(sleep=5.0)
    sb = _backend(handle)
    start = time.monotonic()
    resp = sb.execute("sleep 999", timeout=1)
    assert time.monotonic() - start < 3.0
    assert resp.exit_code == 124
    assert handle.killed


def _patch_base_execute(monkeypatch: pytest.MonkeyPatch, sink: dict[str, Any]) -> None:
    def fake_base_execute(self: Any, command: str, *, timeout: int | None = None) -> Any:
        sink["command"] = command
        sink["timeout"] = timeout
        return SimpleNamespace(output="via-http", exit_code=0, truncated=False)

    monkeypatch.setattr("agent.integrations.langsmith.LangSmithSandbox.execute", fake_base_execute)


async def test_aexecute_ws_connect_failure_falls_back_to_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # run(wait=False) connects eagerly, so a connect failure raises from run().
    sb = _backend(_FakeHandle(), run_raises=SandboxConnectionError("no ws"))
    called: dict[str, Any] = {}
    _patch_base_execute(monkeypatch, called)
    resp = await sb.aexecute("git status", timeout=5)
    assert called == {"command": "git status", "timeout": 5}
    assert resp.output == "via-http"


async def test_aexecute_ws_connect_timeout_falls_back_to_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sb = _backend(_FakeHandle(), run_raises=TimeoutError("connect timed out"))
    called: dict[str, Any] = {}
    _patch_base_execute(monkeypatch, called)
    resp = await sb.aexecute("git status", timeout=5)
    assert called["command"] == "git status"
    assert resp.output == "via-http"


def test_execute_ws_connect_failure_falls_back_to_base(monkeypatch: pytest.MonkeyPatch) -> None:
    sb = _backend(_FakeHandle(), run_raises=SandboxConnectionError("no ws"))
    called: dict[str, Any] = {}
    _patch_base_execute(monkeypatch, called)
    resp = sb.execute("git status", timeout=5)
    assert called == {"command": "git status", "timeout": 5}
    assert resp.output == "via-http"


async def test_aexecute_midstream_ws_drop_falls_back_to_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Connect succeeds (run returns a handle) but the stream drops while draining.
    sb = _backend(_FakeHandle(raises=SandboxConnectionError("dropped")))
    called: dict[str, Any] = {}
    _patch_base_execute(monkeypatch, called)
    resp = await sb.aexecute("git status", timeout=5)
    assert called["command"] == "git status"
    assert resp.output == "via-http"
