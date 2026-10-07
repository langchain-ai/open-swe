import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends.protocol import ExecuteResponse

from agent import background_tasks
from agent.sandboxes import lifecycle
from agent.sandboxes.providers.registry import SandboxGoneError
from agent.sandboxes.state import SANDBOX_BACKENDS, SANDBOX_CONNECTIONS, SandboxUnreachableError
from agent.tasks.store import Task, TaskContext, TaskMembership


@pytest.fixture
def shared_sandbox(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, object]]:
    task = Task(
        coordinator_thread_id="coordinator",
        title="Fix login",
        workspace="default",
    )
    metadata: dict[str, dict[str, object]] = {
        "coordinator": {
            "owner_type": "user",
            "owner_login": "owner",
            "workspace": "default",
            "sandbox_id": "sb-old",
            "github_token_repositories": ["langchain-ai/open-swe"],
        }
    }
    metadata["worker"] = {
        **metadata["coordinator"],
        "task_id": str(task.id),
        "sandbox_host_thread_id": "coordinator",
    }

    async def context(thread_id: str) -> TaskContext:
        return TaskContext(
            task,
            TaskMembership(
                thread_id=thread_id,
                task_id=task.id,
                role="coordinator" if thread_id == "coordinator" else "worker",
            ),
        )

    async def read_metadata(thread_id: str) -> dict[str, object]:
        return metadata[thread_id]

    async def update(thread_id: str, *, metadata: dict[str, object]) -> None:
        shared_metadata[thread_id].update(metadata)

    shared_metadata = metadata
    monkeypatch.setattr(lifecycle, "load_context", context)
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", read_metadata)
    monkeypatch.setattr(lifecycle.client.threads, "update", update)
    monkeypatch.setattr(lifecycle, "configure_git_identity", AsyncMock())
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(return_value=["langchain-ai/open-swe"])
    )
    from agent.sandboxes import tool_access

    monkeypatch.setattr(tool_access, "provision_tool_url", AsyncMock())
    SANDBOX_BACKENDS.clear()
    SANDBOX_CONNECTIONS.clear()
    return metadata


async def test_worker_attaches_to_replaced_host_without_guest_proxy_identity(
    shared_sandbox: dict[str, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_sandbox_id = "sb-replacement"
    shared_sandbox["coordinator"]["sandbox_id"] = host_sandbox_id
    backend = MagicMock(id=host_sandbox_id)
    connect = AsyncMock(return_value=backend)
    refresh = AsyncMock()
    monkeypatch.setattr(lifecycle, "create_sandbox", connect)
    monkeypatch.setattr(lifecycle, "_refresh_github_proxy", refresh)

    attached = await lifecycle.ensure_sandbox_for_thread("worker", workspace_slug="default")

    assert attached.id == host_sandbox_id
    assert SANDBOX_BACKENDS["coordinator"].id == SANDBOX_BACKENDS["worker"].id
    assert shared_sandbox["worker"]["sandbox_id"] == host_sandbox_id
    connect.assert_awaited_once_with(host_sandbox_id)
    assert refresh.await_args.kwargs["thread_id"] == "coordinator"


async def test_worker_refreshes_binding_during_concurrent_host_reconnect(
    shared_sandbox: dict[str, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lifecycle,
        "create_sandbox",
        AsyncMock(side_effect=lambda sandbox_id: MagicMock(id=sandbox_id)),
    )
    monkeypatch.setattr(lifecycle, "_refresh_github_proxy", AsyncMock())
    await lifecycle.ensure_sandbox_for_thread("worker")

    shared_sandbox["coordinator"]["sandbox_id"] = "sb-replacement"
    host = lifecycle.get_cached_sandbox_backend(
        "coordinator", reconnect=lambda: lifecycle.ensure_sandbox_for_thread("coordinator")
    )
    host.start()
    worker = lifecycle.get_cached_sandbox_backend(
        "worker", reconnect=lambda: lifecycle.ensure_sandbox_for_thread("worker")
    )
    worker.start()
    await asyncio.gather(host.ready(), worker.ready())

    assert shared_sandbox["worker"]["sandbox_id"] == "sb-replacement"
    assert worker.id == host.id == "sb-replacement"
    assert worker is not host


async def test_worker_reattach_recovers_a_missed_background_completion(
    shared_sandbox: dict[str, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    shared_sandbox["worker"]["running_background_tasks"] = ["cmd-worker"]
    task: dict[str, object] = {
        "task_id": "cmd-worker",
        "owner_thread_id": "worker",
        "status": "completed",
        "notification": "pending",
    }

    async def get(thread_id: str) -> dict[str, object]:
        return {"metadata": dict(shared_sandbox[thread_id])}

    client = AsyncMock()
    client.threads.get.side_effect = get
    client.threads.update.side_effect = lifecycle.client.threads.update
    backend = MagicMock(id="sb-old")
    backend.aexecute = AsyncMock(return_value=ExecuteResponse(output="", exit_code=0))
    dispatch = AsyncMock()
    monkeypatch.setattr(lifecycle, "create_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(lifecycle, "_refresh_github_proxy", AsyncMock())
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    monkeypatch.setattr(background_tasks, "connect_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(background_tasks, "_list_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(background_tasks, "dispatch_agent_run", dispatch)

    await lifecycle.ensure_sandbox_for_thread("worker")
    await asyncio.gather(*lifecycle._BACKGROUND)

    assert shared_sandbox["worker"]["running_background_tasks"] == []
    assert task["notification"] == "done"
    assert [call.args[0] for call in dispatch.await_args_list] == ["worker"]
    assert "cmd-worker" in dispatch.await_args.args[1]


async def test_worker_does_not_replace_a_deleted_host_sandbox(
    shared_sandbox: dict[str, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lifecycle, "create_sandbox", AsyncMock(side_effect=SandboxGoneError("deleted"))
    )
    create = AsyncMock()
    monkeypatch.setattr(lifecycle, "_create_sandbox_with_proxy", create)

    with pytest.raises(SandboxUnreachableError, match="coordinator must recover"):
        await lifecycle.ensure_sandbox_for_thread("worker", allow_replacement=True)

    create.assert_not_awaited()
    assert "worker" not in SANDBOX_BACKENDS
    assert shared_sandbox["worker"]["sandbox_id"] == "sb-old"


@pytest.mark.parametrize("invalid_binding", ["membership", "repository_scope"])
async def test_worker_cannot_attach_to_a_host_outside_its_permissions(
    shared_sandbox: dict[str, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    invalid_binding: str,
) -> None:
    if invalid_binding == "membership":
        shared_sandbox["worker"]["sandbox_host_thread_id"] = "another-coordinator"
    else:
        shared_sandbox["worker"]["github_token_repositories"] = []
    connect = AsyncMock()
    monkeypatch.setattr(lifecycle, "create_sandbox", connect)

    with pytest.raises(PermissionError):
        await lifecycle.ensure_sandbox_for_thread("worker")

    connect.assert_not_awaited()
    assert "worker" not in SANDBOX_BACKENDS
