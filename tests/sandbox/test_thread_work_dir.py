"""The sandbox work dir is kept with the thread, not with the worker.

Turns rarely land on the same queue worker twice, so a backend object's own
cache is usually cold. The run that probes the work dir records it in the
thread's metadata, and a run on any other worker reuses it without a sandbox
exec, but only for the sandbox it was resolved in.
"""

import logging
from types import SimpleNamespace

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from agent.sandboxes import lifecycle, state
from agent.sandboxes.lifecycle import (
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    resolve_thread_work_dir,
)
from agent.sandboxes.state import SANDBOX_BACKENDS, SANDBOX_CONNECTIONS

_THREAD_ID = "thread-work-dir"


class _Sandbox:
    """One worker's connection to a sandbox; every worker holds its own object."""

    def __init__(self, sandbox_id: str) -> None:
        self._sandbox_id = sandbox_id
        self.commands: list[str] = []

    @property
    def id(self) -> str:
        return self._sandbox_id

    def get_work_dir(self) -> str:
        return "/workspace"

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        del timeout
        self.commands.append(command)
        return ExecuteResponse(output="", exit_code=0)


class _Threads:
    """Thread metadata the way the LangGraph API keeps it: an update merges keys."""

    def __init__(self) -> None:
        self.metadata: dict[str, object] = {}
        self.updates: list[dict[str, object]] = []

    async def get(self, thread_id: str) -> dict[str, object]:
        return {"thread_id": thread_id, "metadata": dict(self.metadata)}

    async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
        assert thread_id == _THREAD_ID
        self.updates.append(metadata)
        self.metadata.update(metadata)


@pytest.fixture(autouse=True)
def _offline_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconnecting refreshes GitHub proxy credentials, which needs the provider's API."""
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")

    async def refreshed(
        sandbox_backend: SandboxBackendProtocol, *_args: object
    ) -> SandboxBackendProtocol:
        return sandbox_backend

    monkeypatch.setattr(lifecycle, "_refresh_github_proxy_or_fail", refreshed)


@pytest.fixture
def threads(monkeypatch: pytest.MonkeyPatch) -> _Threads:
    threads = _Threads()
    client = SimpleNamespace(threads=threads)
    monkeypatch.setattr(lifecycle, "client", client)
    monkeypatch.setattr(state, "get_client", lambda: client)
    return threads


async def _attach_on_new_worker(
    monkeypatch: pytest.MonkeyPatch, sandbox: _Sandbox
) -> SandboxBackendProtocol:
    """Attach the thread's sandbox the way a run does on a worker that never saw it."""
    SANDBOX_BACKENDS.clear()
    SANDBOX_CONNECTIONS.clear()

    async def connect(sandbox_id: str) -> _Sandbox:
        assert sandbox_id == sandbox.id
        return sandbox

    monkeypatch.setattr(lifecycle, "create_sandbox", connect)

    async def reconnect() -> SandboxBackendProtocol:
        return await ensure_sandbox_for_thread(_THREAD_ID)

    return await get_cached_sandbox_backend(_THREAD_ID, reconnect=reconnect).ready()


async def test_probed_work_dir_is_kept_with_the_thread(
    monkeypatch: pytest.MonkeyPatch, threads: _Threads
) -> None:
    threads.metadata = {"sandbox_id": "sb-1"}
    backend = await _attach_on_new_worker(monkeypatch, _Sandbox("sb-1"))

    assert await resolve_thread_work_dir(_THREAD_ID, backend) == "/workspace"

    assert threads.metadata.get("sandbox_work_dir") == {
        "sandbox_id": "sb-1",
        "path": "/workspace",
    }


async def test_new_worker_reuses_the_stored_work_dir_without_an_exec(
    monkeypatch: pytest.MonkeyPatch, threads: _Threads
) -> None:
    threads.metadata = {"sandbox_id": "sb-1"}
    first_worker = await _attach_on_new_worker(monkeypatch, _Sandbox("sb-1"))
    await resolve_thread_work_dir(_THREAD_ID, first_worker)

    sandbox = _Sandbox("sb-1")
    second_worker = await _attach_on_new_worker(monkeypatch, sandbox)
    commands_after_attach = list(sandbox.commands)

    assert await resolve_thread_work_dir(_THREAD_ID, second_worker) == "/workspace"
    assert sandbox.commands == commands_after_attach
    # Only the worker that had to probe writes the thread's metadata.
    assert threads.updates == [
        {"sandbox_work_dir": {"sandbox_id": "sb-1", "path": "/workspace"}},
    ]


async def test_work_dir_stored_for_another_sandbox_is_ignored(
    monkeypatch: pytest.MonkeyPatch, threads: _Threads
) -> None:
    threads.metadata = {
        "sandbox_id": "sb-2",
        "sandbox_work_dir": {"sandbox_id": "sb-1", "path": "/home/old"},
    }
    sandbox = _Sandbox("sb-2")
    backend = await _attach_on_new_worker(monkeypatch, sandbox)
    commands_after_attach = list(sandbox.commands)

    assert await resolve_thread_work_dir(_THREAD_ID, backend) == "/workspace"
    assert sandbox.commands != commands_after_attach
    assert threads.metadata.get("sandbox_work_dir") == {
        "sandbox_id": "sb-2",
        "path": "/workspace",
    }


async def test_failed_metadata_write_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch, threads: _Threads, caplog: pytest.LogCaptureFixture
) -> None:
    threads.metadata = {"sandbox_id": "sb-1"}
    backend = await _attach_on_new_worker(monkeypatch, _Sandbox("sb-1"))

    async def unavailable(**_kwargs: object) -> None:
        raise RuntimeError("LangGraph API unavailable")

    monkeypatch.setattr(threads, "update", unavailable)
    with caplog.at_level(logging.WARNING, logger=lifecycle.__name__):
        assert await resolve_thread_work_dir(_THREAD_ID, backend) == "/workspace"

    assert [getattr(record, "sandbox_id", None) for record in caplog.records] == ["sb-1"]
