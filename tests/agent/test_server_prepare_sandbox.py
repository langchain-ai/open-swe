from unittest.mock import AsyncMock, MagicMock

import pytest

import openswe.server as server
from openswe.middleware import task_coordination
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.tasks.store import Task, TaskContext, TaskDelegation, TaskMembership
from openswe.utils import thread_reads

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_cancelled_worker_wakeup_cannot_reconnect_or_start_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    task = Task(coordinator_thread_id="host", title="Fix login")
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
    monkeypatch.setattr(
        thread_reads.THREADS,
        "get",
        AsyncMock(
            return_value=MagicMock(
                as_langgraph=lambda: {
                    "thread_id": "worker",
                    "metadata": {"owner_type": "user", "owner_login": "owner"},
                }
            )
        ),
    )
    monkeypatch.setattr(
        task_coordination.TaskMembership, "context_for_thread", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(task_coordination.TaskDelegation, "get", AsyncMock(return_value=delegation))
    monkeypatch.setattr(server, "resolve_github_login", AsyncMock(return_value="owner"))
    monkeypatch.setattr(server, "private_credential_login", AsyncMock(return_value="owner"))

    with pytest.raises(PermissionError, match="cancelled"):
        await server.agent_catalog(
            {
                "configurable": {"thread_id": "worker", "source": "dashboard"},
                "metadata": {"kind": "thread_wakeup"},
            },
            ToolSurface(),
        )
