from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends.protocol import ExecuteResponse
from langsmith.sandbox import SandboxRetryableConnectionError

import agent.server as server
from agent.sandboxes import lifecycle
from agent.sandboxes.state import set_sandbox_backend


def _middleware(source: str = "desktop") -> server.PrepareAgentRunMiddleware:
    return server.PrepareAgentRunMiddleware(
        thread_id="thread-1",
        config={"configurable": {"source": source}},
        profile_login=None,
        repo_instructions=None,
        model_id="openai:gpt-5",
        effort=None,
        title_model=MagicMock(),
        source=source,
        user_email="",
        linear_project_id="",
        linear_issue_number="",
        draft_prs=False,
        recent_thread_context_enabled=False,
        admin_workspaces=False,
    )


@pytest.mark.asyncio
async def test_prepare_retries_desktop_sandbox_attach(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = MagicMock()
    proxy.ready = AsyncMock(
        side_effect=[SandboxRetryableConnectionError("gateway unavailable"), "backend"]
    )
    monkeypatch.setattr(server, "get_or_create_sandbox_backend_proxy", lambda _: proxy)
    monkeypatch.setattr(server, "schedule_thread_title_generation", MagicMock())
    monkeypatch.setattr(server, "resolve_sandbox_work_dir", AsyncMock(return_value="/work"))
    monkeypatch.setattr(server, "construct_system_prompt", lambda **_: "prompt")

    result = await _middleware()._prepare({"messages": []}, MagicMock())

    assert result == {"work_dir": "/work", "rendered_system_prompt": "prompt"}
    assert proxy.ready.await_count == 2


@pytest.mark.asyncio
async def test_prepare_notifies_without_sandbox_id_for_retryable_attach_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = SandboxRetryableConnectionError("gateway unavailable")
    proxy = MagicMock()
    proxy.ready = AsyncMock(side_effect=error)
    notify = AsyncMock()
    monkeypatch.setattr(server, "get_or_create_sandbox_backend_proxy", lambda _: proxy)
    monkeypatch.setattr(server, "schedule_thread_title_generation", MagicMock())
    monkeypatch.setattr(server, "post_sandbox_unreachable_notification", notify)
    monkeypatch.setattr(server, "SANDBOX_ATTACH_MAX_ELAPSED", 0)

    with pytest.raises(SandboxRetryableConnectionError):
        await _middleware()._prepare({"messages": []}, MagicMock())

    notify.assert_awaited_once_with(
        {"configurable": {"source": "desktop", "draft_prs": False}}, sandbox_id=None
    )


class _Sandbox:
    id = "sb-1"

    def get_work_dir(self) -> str:
        return "/workspace"

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output="", exit_code=0)


class _Threads:
    def __init__(self) -> None:
        self.metadata: dict[str, object] = {}

    async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
        self.metadata.update(metadata)


@pytest.mark.asyncio
async def test_prepare_keeps_the_probed_work_dir_with_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next run may land on any worker; it finds the work dir on the thread."""
    threads = _Threads()
    monkeypatch.setattr(server, "client", SimpleNamespace(threads=threads))
    monkeypatch.setattr(lifecycle, "client", SimpleNamespace(threads=threads))
    monkeypatch.setattr(server, "schedule_thread_title_generation", MagicMock())
    monkeypatch.setattr(
        server, "resolve_github_token", AsyncMock(return_value=("installation-token", None))
    )
    monkeypatch.setattr(server, "_resolve_prompt_default_repo", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "resolve_triggering_user_identity", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "load_workspace", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "construct_system_prompt", lambda **_: "prompt")
    set_sandbox_backend("thread-1", _Sandbox())

    result = await _middleware(source="dashboard")._prepare({"messages": []}, MagicMock())

    assert result["work_dir"] == "/workspace"
    assert threads.metadata.get("sandbox_work_dir") == {
        "sandbox_id": "sb-1",
        "path": "/workspace",
    }
