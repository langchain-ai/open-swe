from unittest.mock import AsyncMock, MagicMock

import pytest
from langsmith.sandbox import SandboxRetryableConnectionError

import agent.server as server
from agent.middleware import task_coordination
from agent.tasks.store import Task, TaskContext, TaskDelegation, TaskMembership


def _middleware() -> server.PrepareAgentRunMiddleware:
    return server.PrepareAgentRunMiddleware(
        thread_id="thread-1",
        config={"configurable": {"source": "desktop"}},
        profile_login=None,
        repo_instructions=None,
        model_id="openai:gpt-5",
        effort=None,
        title_model=MagicMock(),
        source="desktop",
        user_email="",
        linear_project_id="",
        linear_issue_number="",
        draft_prs=False,
        recent_thread_context_enabled=False,
        admin_workspaces=False,
    )


async def test_cancelled_worker_wakeup_cannot_reconnect_or_start_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    task = Task(coordinator_thread_id="host", title="Fix login", workspace="default")
    context = TaskContext(task, TaskMembership(thread_id="worker", task_id=task.id, role="worker"))
    delegation = TaskDelegation(
        worker_thread_id="worker",
        task_id=task.id,
        coordinator_thread_id="host",
        instructions="Fix login",
        model="openai:gpt-5.5",
        effort="high",
        cancelled=True,
    )
    client = MagicMock()
    client.threads.get = AsyncMock(
        return_value={"metadata": {"owner_type": "user", "owner_login": "owner"}}
    )
    monkeypatch.setattr(task_coordination.langgraph_sdk, "get_client", lambda: client)
    monkeypatch.setattr(
        task_coordination, "task_coordination_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(task_coordination, "load_context", AsyncMock(return_value=context))
    monkeypatch.setattr(task_coordination, "get_delegation", AsyncMock(return_value=delegation))
    monkeypatch.setattr(server, "graph_loaded_for_execution", lambda _: True)
    monkeypatch.setattr(server, "resolve_github_login", AsyncMock(return_value="owner"))
    monkeypatch.setattr(server, "private_credential_login", AsyncMock(return_value="owner"))
    backend = MagicMock(side_effect=AssertionError("Cancelled worker reached sandbox startup"))
    monkeypatch.setattr(server, "get_cached_sandbox_backend", backend)

    with pytest.raises(PermissionError, match="cancelled"):
        await server.get_agent(
            {
                "configurable": {"thread_id": "worker", "source": "dashboard"},
                "metadata": {"kind": "thread_wakeup"},
            }
        )

    backend.assert_not_called()


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
