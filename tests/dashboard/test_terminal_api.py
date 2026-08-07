from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.dashboard import terminal_api
from agent.integrations.langsmith import TimeoutLangSmithSandbox


class FakeHandle:
    command_id = "command"
    pid = 123
    result = SimpleNamespace(exit_code=0)

    def __iter__(self):
        return iter(())

    def kill(self) -> None:
        pass

    def send_input(self, data: str) -> None:
        pass


async def test_terminal_create_is_owner_only_and_resolves_repo_cwd(monkeypatch) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "owner",
        "sandbox_id": "sandbox",
        "repo_owner": "org",
        "repo_name": "repo",
    }

    async def authorize(thread_id: str, login: str, *, email: str | None = None):
        if login != "owner":
            raise HTTPException(404, "thread not found")
        return {"thread_id": thread_id, "metadata": metadata}

    backend = object.__new__(TimeoutLangSmithSandbox)
    sandbox = SimpleNamespace(run=lambda *args, **kwargs: FakeHandle())
    cast(Any, backend)._sandbox = sandbox
    proxy = SimpleNamespace(
        aexecute=AsyncMock(return_value=SimpleNamespace(exit_code=0)),
    )
    monkeypatch.setattr(terminal_api, "_authorized_thread", authorize)
    monkeypatch.setattr(terminal_api, "get_sandbox_backend", AsyncMock(return_value=proxy))
    monkeypatch.setattr(terminal_api, "unwrap_sandbox_backend", lambda value: backend)
    monkeypatch.setattr(
        terminal_api, "aresolve_repo_dir", AsyncMock(return_value="/workspace/repo")
    )

    manager = terminal_api.TerminalManager()
    with pytest.raises(HTTPException) as exc:
        await manager.create("thread", "teammate")
    assert exc.value.status_code == 404

    terminal = await manager.create("thread", "owner")
    assert terminal["cwd"] == "/workspace/repo"
    proxy.aexecute.assert_awaited_once_with("test -d /workspace/repo", timeout=30)
    await manager.aclose()
