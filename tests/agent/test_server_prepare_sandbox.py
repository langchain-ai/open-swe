from unittest.mock import AsyncMock, MagicMock

import pytest

import openswe.server as server
from openswe.middleware import task_coordination
from openswe.tasks.store import Task, TaskContext, TaskDelegation, TaskMembership
from openswe.utils import thread_reads
from openswe.workspaces.rows import WorkspaceRow


async def test_cancelled_worker_wakeup_cannot_reconnect_or_start_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    workspace = WorkspaceRow(slug="default", name="Default")
    task = Task(coordinator_thread_id="host", title="Fix login", workspace_id=workspace.id)
    task.workspace = workspace
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
    monkeypatch.setattr(thread_reads.langgraph_sdk, "get_client", lambda: client)
    monkeypatch.setattr(
        task_coordination, "task_coordination_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        task_coordination.TaskMembership, "context_for_thread", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(task_coordination.TaskDelegation, "get", AsyncMock(return_value=delegation))
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
