"""Getting work back out of a thread, and stopping one that will not stop.

Two endpoints for a stuck thread: download the sandbox's uncommitted work as a
patch, or interrupt every live run on it (as the owner, or as an admin for any
thread).
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import authz, thread_api
from agent.dashboard.routes import admin as admin_routes

# The endpoint's own guardrails on the generated patch.
_PATCH_TIMEOUT_SECONDS = 120
_PATCH_LIMIT_BYTES = 25 * 1024 * 1024

_OWNED_SANDBOX_THREAD = {
    "source": "dashboard",
    "github_login": "octocat",
    "sandbox_id": "sbx",
    "repo_owner": "octo",
    "repo_name": "repo",
    "base_branch": "main",
}


def _install_client(monkeypatch, **kwargs: Any) -> FakeLangGraphClient:
    client = FakeLangGraphClient(**kwargs)
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    return client


class _RecoverySandbox:
    """Runs the patch script, then hands back the file it claims to have written."""

    def __init__(
        self,
        *,
        size: int = 11,
        content: bytes = b"patch bytes",
        path: str = "/tmp/open-swe-tid.patch",
    ) -> None:
        self.commands: list[str] = []
        self.timeouts: list[int | None] = []
        self.downloaded: list[list[str]] = []
        self._payload = {"ok": True, "path": path, "size": size}
        self._content = content

    async def aexecute(self, command: str, *, timeout: int | None = None) -> SimpleNamespace:
        self.commands.append(command)
        self.timeouts.append(timeout)
        return SimpleNamespace(output=json.dumps(self._payload), exit_code=0)

    async def adownload_files(self, paths: list[str]) -> list[SimpleNamespace]:
        self.downloaded.append(list(paths))
        return [SimpleNamespace(content=self._content)]


def _install_sandbox(monkeypatch, sandbox: _RecoverySandbox) -> _RecoverySandbox:
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(return_value=sandbox))
    return sandbox


async def test_recovery_patch_requires_thread_owner(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        thread_metadata={"source": "dashboard", "github_login": "owner", "sandbox_id": "sbx"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_recovery_patch("tid", "intruder")

    assert exc_info.value.status_code == 404


async def test_recovery_patch_requires_sandbox(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "octocat"})

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_recovery_patch("tid", "octocat")

    assert exc_info.value.status_code == 404
    assert "sandbox" in exc_info.value.detail


async def test_recovery_patch_downloads_generated_patch(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata=dict(_OWNED_SANDBOX_THREAD))
    sandbox = _install_sandbox(monkeypatch, _RecoverySandbox())

    content, filename = await thread_api.get_dashboard_thread_recovery_patch("tid", "octocat")

    assert content == b"patch bytes"
    assert filename == "open-swe-tid.patch"
    assert sandbox.downloaded == [["/tmp/open-swe-tid.patch"]]
    assert sandbox.timeouts == [_PATCH_TIMEOUT_SECONDS]
    assert "repo" in sandbox.commands[0]


async def test_recovery_patch_searches_command_cwd_before_workspace_fallback(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata=dict(_OWNED_SANDBOX_THREAD))
    sandbox = _install_sandbox(monkeypatch, _RecoverySandbox())

    await thread_api.get_dashboard_thread_recovery_patch("tid", "octocat")

    command = sandbox.commands[0]
    assert "Path.cwd().resolve()" in command
    assert "WORKSPACE_FALLBACK = Path('/workspace')" in command
    assert "roots = [Path.cwd().resolve(), WORKSPACE_FALLBACK]" in command


async def test_recovery_patch_rejects_empty_patch(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata=dict(_OWNED_SANDBOX_THREAD))
    _install_sandbox(monkeypatch, _RecoverySandbox(size=0))

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_recovery_patch("tid", "octocat")

    assert exc_info.value.status_code == 404
    assert "changes" in exc_info.value.detail


async def test_recovery_patch_enforces_size_limit(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata=dict(_OWNED_SANDBOX_THREAD))
    _install_sandbox(monkeypatch, _RecoverySandbox(size=_PATCH_LIMIT_BYTES + 1))

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_recovery_patch("tid", "octocat")

    assert exc_info.value.status_code == 413


_ACTIVE_RUNS = [
    {"run_id": "pending-run", "status": "pending"},
    {"run_id": "running-run", "status": "running"},
]
_INTERRUPT_ALL_ACTIVE = {
    "thread_id": "thread-1",
    "run_ids": ["pending-run", "running-run"],
    "action": "interrupt",
}


async def test_cancel_thread_interrupts_runs_it_did_not_start(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        threads=[
            {
                "thread_id": "thread-1",
                "status": "busy",
                "metadata": {
                    "title": "Slack-triggered thread",
                    "github_login": "owner",
                    "latest_run_status": "running",
                    "updated_at_ms": 1,
                },
            }
        ],
        runs={"thread-1": _ACTIVE_RUNS},
    )

    result = await thread_api.cancel_dashboard_thread("thread-1", "owner")

    assert client.runs.cancelled == [_INTERRUPT_ALL_ACTIVE]
    # Reported as interrupted even though the platform still says busy.
    assert result["status"] == "interrupted"


async def test_cancel_thread_rejects_non_owner(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        threads=[
            {
                "thread_id": "thread-1",
                "status": "busy",
                "metadata": {"github_login": "owner"},
            }
        ],
        runs={"thread-1": _ACTIVE_RUNS},
    )

    with pytest.raises(HTTPException):
        await thread_api.cancel_dashboard_thread("thread-1", "someone-else")

    assert client.runs.cancelled == []
    assert client.threads.updates == []


async def test_admin_cancel_thread_interrupts_all_active_runs(monkeypatch) -> None:
    thread = {
        "thread_id": "thread-1",
        "status": "busy",
        "metadata": {
            "title": "Runaway thread",
            "latest_run_status": "running",
            "updated_at_ms": 1,
        },
    }
    client = _install_client(monkeypatch, threads=[thread], runs={"thread-1": _ACTIVE_RUNS})

    result = await thread_api.admin_cancel_dashboard_thread("thread-1")

    assert client.runs.cancelled == [_INTERRUPT_ALL_ACTIVE]
    # The metadata write lands only after the cancel succeeds.
    assert client.call_names.index("threads.update") > client.call_names.index("runs.cancel_many")
    assert thread["metadata"]["latest_run_status"] == "interrupted"
    assert result["id"] == "thread-1"


async def test_admin_cancel_thread_does_not_update_on_cancel_failure(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        threads=[{"thread_id": "thread-1", "status": "busy", "metadata": {}}],
        runs={"thread-1": _ACTIVE_RUNS},
    )
    client.runs.cancel_error = RuntimeError("runtime unavailable")

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.admin_cancel_dashboard_thread("thread-1")

    assert exc_info.value.status_code == 502
    assert client.threads.updates == []


async def test_admin_cancel_thread_route_delegates_without_owner_identity(monkeypatch) -> None:
    cancel = AsyncMock(return_value={"id": "thread-1", "status": "interrupted"})
    monkeypatch.setattr(admin_routes, "admin_cancel_dashboard_thread", cancel)

    result = await admin_routes.admin_cancel_thread("thread-1", _admin={"sub": "admin"})

    assert result == {"id": "thread-1", "status": "interrupted"}
    cancel.assert_awaited_once_with("thread-1")


def test_admin_cancel_thread_dependency_rejects_non_admin(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")

    with pytest.raises(HTTPException) as exc_info:
        authz.require_admin({"sub": "not-admin", "email": "user@example.com"})

    assert exc_info.value.status_code == 403
