import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from deepagents.backends.protocol import ExecuteResponse

from openswe import background_tasks
from openswe.sandboxes import lifecycle, managed, state
from openswe.sandboxes.providers.registry import SandboxGoneError
from openswe.sandboxes.state import SANDBOX_BACKENDS, SANDBOX_CONNECTIONS, SandboxUnreachableError
from openswe.tasks import service as task_service
from openswe.tasks.store import Task, TaskContext, TaskMembership
from openswe.users import User
from openswe.users.models import UserIdentity
from openswe.workspaces.rows import WorkspaceRow


@pytest.fixture
def shared_sandbox(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, object]]:
    workspace = WorkspaceRow(slug="default", name="Default")
    task = Task(
        coordinator_thread_id="coordinator",
        title="Fix login",
        workspace_id=workspace.id,
    )
    task.workspace = workspace
    owner = User(identities=[UserIdentity(provider="github", external_id="1", login="owner")])
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=owner))
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=owner))
    monkeypatch.setattr(User, "get", AsyncMock(return_value=owner))
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
    monkeypatch.setattr(TaskMembership, "context_for_thread", context)
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", read_metadata)
    monkeypatch.setattr(managed, "get_sandbox_metadata", read_metadata)
    monkeypatch.setattr(state, "get_sandbox_metadata", read_metadata)
    monkeypatch.setattr(lifecycle.client.threads, "update", update)
    monkeypatch.setattr(lifecycle, "configure_git_identity", AsyncMock())
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(return_value=["langchain-ai/open-swe"])
    )
    SANDBOX_BACKENDS.clear()
    SANDBOX_CONNECTIONS.clear()
    return metadata


@pytest.mark.parametrize("owner_type", ["user", "system"])
async def test_worker_attaches_to_replaced_host_without_guest_proxy_identity(
    shared_sandbox: dict[str, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    owner_type: str,
) -> None:
    if owner_type == "system":
        for metadata in shared_sandbox.values():
            metadata["owner_type"] = "system"
            metadata.pop("owner_login")
        for method in ("get", "for_login", "for_identity"):
            monkeypatch.setattr(User, method, AsyncMock(side_effect=AssertionError("User lookup")))
    host_sandbox_id = "sb-replacement"
    shared_sandbox["coordinator"]["sandbox_id"] = host_sandbox_id
    backend = MagicMock(id=host_sandbox_id)
    connect = AsyncMock(return_value=backend)
    refresh = AsyncMock()
    monkeypatch.setattr(lifecycle, "connect_existing_sandbox", connect)
    monkeypatch.setattr(lifecycle, "_refresh_github_proxy", refresh)

    attached = await lifecycle.ensure_sandbox_for_thread("worker", workspace_slug="default")

    assert attached.id == host_sandbox_id
    assert SANDBOX_BACKENDS["coordinator"].id == SANDBOX_BACKENDS["worker"].id
    assert shared_sandbox["worker"]["sandbox_id"] == host_sandbox_id
    connect.assert_awaited_once_with(host_sandbox_id)
    assert refresh.await_args.kwargs["thread_id"] == "coordinator"
    if owner_type == "system":
        assert all("owner_user_id" not in metadata for metadata in shared_sandbox.values())
    else:
        assert (
            shared_sandbox["worker"]["owner_user_id"]
            == shared_sandbox["coordinator"]["owner_user_id"]
        )


async def test_worker_refreshes_binding_during_concurrent_host_reconnect(
    shared_sandbox: dict[str, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lifecycle,
        "connect_existing_sandbox",
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


@pytest.mark.usefixtures("memory_event_claims")
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
    monkeypatch.setattr(lifecycle, "connect_existing_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(lifecycle, "_refresh_github_proxy", AsyncMock())
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    monkeypatch.setattr(background_tasks, "connect_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(background_tasks, "_list_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(background_tasks, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(
        task_service,
        "recipient_config",
        AsyncMock(return_value={"thread_id": "worker", "github_login": "owner"}),
    )

    await lifecycle.ensure_sandbox_for_thread("worker")
    await asyncio.gather(*lifecycle._BACKGROUND)

    assert shared_sandbox["worker"]["running_background_tasks"] == []
    assert task["notification"] == "done"
    assert [call.args[0] for call in dispatch.await_args_list] == ["worker"]
    assert "cmd-worker" in dispatch.await_args.args[1]


async def test_worker_does_not_hand_over_a_deleted_host_sandbox(
    shared_sandbox: dict[str, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lifecycle, "connect_existing_sandbox", AsyncMock(side_effect=SandboxGoneError("deleted"))
    )

    with pytest.raises(SandboxUnreachableError, match="coordinator must recover"):
        await lifecycle.ensure_sandbox_for_thread("worker", abandon_unreachable=True)

    assert "worker" not in SANDBOX_BACKENDS
    assert shared_sandbox["worker"]["sandbox_id"] == "sb-old"


@pytest.mark.parametrize("owner_type", ["user", "system"])
@pytest.mark.parametrize(
    "invalid_binding",
    ["membership", "repository_scope", "workspace", "owner", "owner_type", "admin", "visibility"],
)
async def test_worker_cannot_attach_to_a_host_outside_its_permissions(
    shared_sandbox: dict[str, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    invalid_binding: str,
    owner_type: str,
) -> None:
    for metadata in shared_sandbox.values():
        metadata["owner_type"] = owner_type
    if invalid_binding == "owner_type":
        shared_sandbox["coordinator"]["owner_type"] = "system" if owner_type == "user" else "user"
    elif invalid_binding == "admin":
        shared_sandbox["worker"]["admin_thread"] = True
    elif invalid_binding == "visibility":
        shared_sandbox["worker"]["visibility"] = "private"
    elif invalid_binding == "membership":
        shared_sandbox["worker"]["sandbox_host_thread_id"] = "another-coordinator"
    elif invalid_binding == "workspace":
        shared_sandbox["worker"]["workspace"] = "another-workspace"
    elif invalid_binding == "owner":
        if owner_type == "system":
            for metadata in shared_sandbox.values():
                metadata["visibility"] = "private"
        else:
            shared_sandbox["worker"]["owner_user_id"] = str(uuid4())
            monkeypatch.setattr(User, "get", AsyncMock(return_value=User()))
    else:
        shared_sandbox["worker"]["github_token_repositories"] = []
    connect = AsyncMock()
    monkeypatch.setattr(lifecycle, "connect_existing_sandbox", connect)

    with pytest.raises(PermissionError):
        await lifecycle.ensure_sandbox_for_thread("worker")

    connect.assert_not_awaited()
    assert "worker" not in SANDBOX_BACKENDS


async def test_worker_catalog_attaches_to_the_coordinators_mda_sandbox(
    shared_sandbox: dict[str, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    shared_sandbox["coordinator"]["sandbox_owner"] = "mda"
    shared_sandbox["worker"].pop("sandbox_id")
    monkeypatch.setattr(
        lifecycle, "connect_existing_sandbox", AsyncMock(return_value=MagicMock(id="sb-old"))
    )
    monkeypatch.setattr(lifecycle, "_refresh_github_proxy", AsyncMock())
    monkeypatch.setattr(managed, "mda_github_proxy", AsyncMock(return_value=None))
    monkeypatch.setattr(
        managed.SandboxCreateConfig,
        "for_thread",
        AsyncMock(return_value=lifecycle.SandboxCreateConfig(snapshot_id=None)),
    )

    sandbox = await managed.ManagedSandbox.for_thread("worker", "default")

    assert sandbox is not None
    assert sandbox.thread_id == "coordinator"
    assert sandbox.sandbox_id == "sb-old"


async def test_worker_catalog_fails_when_the_coordinator_is_unattached(
    shared_sandbox: dict[str, dict[str, object]],
) -> None:
    shared_sandbox["coordinator"].pop("sandbox_id")
    shared_sandbox["coordinator"]["sandbox_owner"] = "mda"
    with pytest.raises(SandboxUnreachableError, match="coordinator must attach"):
        await managed.ManagedSandbox.for_thread("worker", "default")


@pytest.mark.parametrize("cached", [False, True])
async def test_worker_download_and_push_use_the_coordinator_on_a_new_replica(
    shared_sandbox: dict[str, dict[str, object]], monkeypatch: pytest.MonkeyPatch, cached: bool
) -> None:
    from importlib import import_module

    from openswe.middleware.workflow_push_guard import WorkflowPushGuardMiddleware
    from tests.agent.test_workflow_push_guard import _Backend, _Request

    download = import_module("openswe.tools.create_sandbox_file_download_url")
    backend = _Backend(workflow_files="")
    backend.id = "sb-old"
    monkeypatch.setattr(lifecycle, "connect_existing_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(lifecycle, "_refresh_github_proxy", AsyncMock())
    monkeypatch.setattr(
        "openswe.run_config.get_config", lambda: {"configurable": {"thread_id": "worker"}}
    )
    monkeypatch.setattr(download, "resolve_sandbox_work_dir", AsyncMock(return_value="/repo"))
    original_execute = backend.aexecute
    monkeypatch.setattr(
        backend,
        "aexecute",
        AsyncMock(return_value=ExecuteResponse(output="/repo/changes.patch", exit_code=0)),
    )
    resolved, path, _ = await download.resolve_sandbox_file("changes.patch")
    assert resolved.id == "sb-old"
    assert path == "/repo/changes.patch"

    SANDBOX_BACKENDS.clear()
    shared_sandbox["worker"]["sandbox_id"] = "stale-worker-sandbox"
    if cached:
        state.set_sandbox_backend("worker", MagicMock(id="stale-worker-sandbox"))
    monkeypatch.setattr(backend, "aexecute", original_execute)
    request = _Request("git -C /repo push origin feature")
    monkeypatch.setattr("openswe.middleware.workflow_push_guard._thread_id", lambda _: "worker")
    handler = AsyncMock(return_value="pushed")
    result = await WorkflowPushGuardMiddleware().awrap_tool_call(request, handler)
    assert result == "pushed"
    assert backend.commands
    assert SANDBOX_BACKENDS["worker"].id == SANDBOX_BACKENDS["coordinator"].id == "sb-old"
