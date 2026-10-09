import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy import func, select

from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.database import postgres
from openswe.tasks import flags, presentation, service, store
from openswe.tasks import messages as task_messages
from openswe.tasks.messages import TaskMessage
from openswe.tasks.presentation import TaskEventMetadata
from openswe.tasks.schemas import ThreadMetadata
from openswe.threads import access, creation, handlers
from openswe.users import User
from openswe.users.models import UserIdentity
from openswe.webhooks import event_matches
from openswe.webhooks.event_matches import EventMatch
from openswe.webhooks.event_subscriptions import EventSubscription
from openswe.workspaces.rows import WorkspaceRow

MODEL = "openai:gpt-6-astra"
COORDINATOR = str(uuid4())
OWNER = "owner"
WORKSPACE_MODEL = "anthropic:claude-sonnet-5-5"


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({"source": "slack", "model": MODEL, "effort": "high"}, id="slack"),
        pytest.param(
            {
                "model": MODEL,
                "effort": "high",
                "resolved_model": WORKSPACE_MODEL,
                "resolved_effort": "low",
            },
            id="switched-model",
        ),
        pytest.param({"resolved_model": MODEL, "resolved_effort": "high"}, id="legacy"),
        pytest.param({"agent_settings": {"model_id": MODEL, "effort": "high"}}, id="saved"),
        pytest.param(
            {
                "model": WORKSPACE_MODEL,
                "effort": "low",
                "resolved_model": WORKSPACE_MODEL,
                "resolved_effort": "low",
                "agent_settings": {
                    "model_id": MODEL,
                    "effort": "high",
                    "requested_model": MODEL,
                    "model_handoff_complete": True,
                    "model_routing_enabled": False,
                },
            },
            id="opening-model-handoff",
        ),
    ],
)
async def test_worker_inherits_coordinator_model_and_effort(
    monkeypatch: pytest.MonkeyPatch, metadata: dict[str, object]
) -> None:
    settings = WorkspaceSettings(
        {"default_agent_model": WORKSPACE_MODEL, "default_agent_reasoning_effort": "low"}
    )
    monkeypatch.setattr(service, "get_workspace_settings", AsyncMock(return_value=settings))

    assert await service.model_choice("default", metadata, None, None) == (MODEL, "high")


async def test_worker_model_overrides_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = WorkspaceSettings(
        {"default_agent_model": WORKSPACE_MODEL, "default_agent_reasoning_effort": "low"}
    )
    monkeypatch.setattr(service, "get_workspace_settings", AsyncMock(return_value=settings))
    metadata: dict[str, object] = {"model": MODEL, "effort": "high"}

    assert await service.model_choice("default", metadata, None, "max") == (MODEL, "max")
    assert await service.model_choice("default", metadata, WORKSPACE_MODEL, None) == (
        WORKSPACE_MODEL,
        "low",
    )
    assert await service.model_choice("default", {}, None, None) == (WORKSPACE_MODEL, "low")
    assert await service.model_choice(
        "default", {"model": "anthropic:claude-fable-5-1", "effort": "high"}, None, None
    ) == (WORKSPACE_MODEL, "low")
    with pytest.raises(ValueError, match="not supported"):
        await service.model_choice("default", metadata, None, "none")


def task() -> store.Task:
    workspace = WorkspaceRow(slug="default", name="Default")
    task = store.Task(
        coordinator_thread_id=COORDINATOR,
        title="Fix login",
        workspace_id=workspace.id,
    )
    task.workspace = workspace
    return task


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    metadata: dict[str, dict[str, JsonValue]] = {
        COORDINATOR: {
            "owner_type": "user",
            "owner_login": OWNER,
            "visibility": "public",
            "source": "dashboard",
            "workspace": "default",
            "sandbox_id": "shared-sandbox",
            "resolved_model": MODEL,
            "resolved_effort": "low",
            "github_token_repositories": ["langchain-ai/open-swe"],
            "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
        }
    }
    statuses: dict[str, str] = {}

    async def get(thread_id: str) -> dict[str, object]:
        return {
            "thread_id": thread_id,
            "metadata": metadata[thread_id],
            "status": statuses.get(thread_id, "idle"),
        }

    async def create(
        *, thread_id: str, metadata: Mapping[str, JsonValue], if_exists: str
    ) -> dict[str, object]:
        client.metadata.setdefault(thread_id, dict(metadata))
        return await get(thread_id)

    async def run_create(thread_id: str, assistant_id: str, **kwargs: object) -> dict[str, object]:
        statuses[thread_id] = "busy"
        result = {
            "thread_id": thread_id,
            "run_id": str(uuid4()),
            "status": "pending",
            "assistant_id": assistant_id,
            "config": kwargs.get("config", {}),
            "metadata": kwargs.get("metadata", {}),
            "input": kwargs.get("input", {}),
        }
        client.created_runs.append(result)
        if client.fail_after_accept:
            client.fail_after_accept = False
            raise ConnectionError("response lost after acceptance")
        return result

    async def list_runs(thread_id: str, **kwargs: object) -> list[dict[str, object]]:
        return [
            run
            for run in client.created_runs
            if run["thread_id"] == thread_id
            and (not kwargs.get("status") or run["status"] == kwargs["status"])
        ]

    async def cancel_many(*, thread_id: str, run_ids: list[str], action: str) -> None:
        for run in client.created_runs:
            if run["thread_id"] == thread_id and run["run_id"] in run_ids:
                run["status"] = "interrupted"
        statuses[thread_id] = "idle"

    client.metadata = metadata
    client.statuses = statuses
    client.created_runs = []
    client.fail_after_accept = False
    client.threads.get = AsyncMock(side_effect=get)
    client.threads.create = AsyncMock(side_effect=create)

    async def update(thread_id: str, *, metadata: dict[str, JsonValue]) -> None:
        client.metadata[thread_id].update(metadata)

    client.threads.update = AsyncMock(side_effect=update)
    owner = User(
        identities=[UserIdentity(provider="github", external_id="1", login=OWNER)],
        preferences={"experimental_task_coordination": True},
    )
    client.owner = owner
    monkeypatch.setattr(
        User,
        "for_login",
        AsyncMock(side_effect=lambda provider, login: owner if login == OWNER else None),
    )
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=owner))
    monkeypatch.setattr(User, "get", AsyncMock(return_value=owner))
    client.threads.get_state = AsyncMock(return_value={"values": {"messages": []}})
    client.runs.create = AsyncMock(side_effect=run_create)
    client.runs.list = AsyncMock(side_effect=list_runs)
    client.runs.cancel_many = AsyncMock(side_effect=cancel_many)
    monkeypatch.setattr(service, "langgraph_client", lambda: client)
    monkeypatch.setattr(presentation, "langgraph_client", lambda: client)
    monkeypatch.setattr(event_matches, "dispatch_client", lambda: client)
    monkeypatch.setattr(task_messages, "dispatch_client", lambda: client)
    monkeypatch.setattr(service, "enforce_github_login_gate", AsyncMock())
    monkeypatch.setattr(service, "get_profile", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "resolve_run_email", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "COMPLETION_WEBHOOK_URL", "https://example.test/completion")
    monkeypatch.setattr(creation, "_owner_prefers_tools_in_sandbox", AsyncMock(return_value=False))
    from openswe import dispatch
    from openswe.slack import thinking

    monkeypatch.setattr(dispatch, "_run_user_id", AsyncMock(return_value=None))
    monkeypatch.setattr(thinking, "sync_slack_background_status", AsyncMock())
    monkeypatch.setattr(service, "interrupt_transcript_turns", AsyncMock())
    monkeypatch.setattr(service, "cancel_thread_wakeups", AsyncMock())
    return client


@pytest.mark.usefixtures("registry_db")
async def test_default_off_rejects_delegation_without_creating_a_task(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.owner.preferences = {"experimental_task_coordination": False}
    with pytest.raises(PermissionError, match="disabled"):
        await service.spawn_worker(
            service.Actor(COORDINATOR, OWNER),
            instructions="Implement",
            model=None,
            effort=None,
            request_id="disabled-call",
        )
    assert await store.TaskMembership.context_for_thread(COORDINATOR) is None
    assert not client.created_runs


@pytest.mark.parametrize("bridge_client", ["cli", None])
async def test_cli_bridge_rejects_delegation_before_reserving_a_worker(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, bridge_client: str | None
) -> None:
    client.metadata[COORDINATOR]["sandbox_id"] = "bridge:cli"
    if bridge_client is not None:
        client.metadata[COORDINATOR]["sandbox_bridge_client"] = bridge_client
    monkeypatch.setattr(store.TaskMembership, "context_for_thread", AsyncMock(return_value=None))
    reserve = AsyncMock()
    monkeypatch.setattr(store.Task, "reserve_worker", reserve)

    with pytest.raises(ValueError, match="one-shot CLI bridge"):
        await service.spawn_worker(
            service.Actor(COORDINATOR, OWNER),
            instructions="Implement",
            model=None,
            effort=None,
            request_id="cli-call",
        )

    reserve.assert_not_awaited()
    assert not client.created_runs


@pytest.mark.usefixtures("registry_db")
async def test_observer_relays_to_the_coordinator_only_where_its_sender_could_post(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    observer = str(uuid4())
    client.metadata[observer] = {
        "owner_type": "user",
        "owner_login": "chat-opener",
        "visibility": "public",
        "source": "dashboard",
        "workspace": "default",
    }
    founded = await store.Task.add_observer(
        COORDINATOR, observer, title="Fix login", workspace="default"
    )
    rejoined = await store.Task.add_observer(
        COORDINATOR, observer, title="Fix login", workspace="default"
    )
    assert rejoined.id == founded.id
    context = await store.TaskMembership.context_for_thread(COORDINATOR)
    assert context is not None and context.membership.role == "coordinator"
    monkeypatch.setattr(TaskMessage, "deliver", AsyncMock())
    reviewer = service.Actor(observer, "reviewer")

    relayed = await service.message_task_thread(reviewer, "Rename the flag", request_id="call-1")

    assert relayed["recipient_thread_id"] == COORDINATOR
    with pytest.raises(PermissionError, match="coordinator"):
        await service.message_task_thread(
            reviewer, "Rename the flag", request_id="call-2", worker_thread_id=COORDINATOR
        )
    client.metadata[COORDINATOR]["visibility"] = "private"
    with pytest.raises(HTTPException):
        await service.message_task_thread(reviewer, "Rename the flag", request_id="call-3")


async def test_public_thread_does_not_allow_using_owners_credentials(client: MagicMock) -> None:
    with pytest.raises(PermissionError, match="thread owner"):
        await service.authorized_metadata(service.Actor(COORDINATOR, "other-user"))


@pytest.mark.parametrize("enabled", [True, False])
async def test_task_owner_id_survives_rename_and_rejects_reassigned_login(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    metadata = await service.authorized_metadata(service.Actor(COORDINATOR, OWNER))
    owner_id = metadata.owner_user_id
    assert owner_id is not None
    assert client.metadata[COORDINATOR]["owner_user_id"] == str(owner_id)
    owner = User(
        id=owner_id,
        identities=[UserIdentity(provider="github", external_id="1", login="renamed-owner")],
        preferences={"experimental_task_coordination": enabled},
    )
    replacement = User(
        identities=[UserIdentity(provider="github", external_id="2", login=OWNER)],
        preferences={"experimental_task_coordination": not enabled},
    )
    monkeypatch.setattr(
        User,
        "for_login",
        AsyncMock(
            side_effect=lambda provider, login: owner if login == "renamed-owner" else replacement
        ),
    )
    monkeypatch.setattr(
        User,
        "for_identity",
        AsyncMock(
            side_effect=lambda provider, external_id: owner if external_id == "1" else replacement
        ),
    )
    monkeypatch.setattr(User, "get", AsyncMock(return_value=owner))
    client.metadata[COORDINATOR]["visibility"] = "private"
    assert (
        await service.authorized_metadata(service.Actor(COORDINATOR, "renamed-owner"))
    ).owner_user_id == owner_id
    assert not await flags.task_coordination_enabled(OWNER, owner_user_id=owner_id)
    assert not await flags.task_coordination_enabled("renamed-owner")
    with pytest.raises(PermissionError, match="disabled"):
        await flags.require_task_coordination(client.metadata[COORDINATOR])
    assert owner.typed_preferences.experimental_task_coordination is enabled
    with pytest.raises(PermissionError, match="thread owner"):
        await service.authorized_metadata(service.Actor(COORDINATOR, OWNER))
    monkeypatch.setattr(User, "get", AsyncMock(return_value=None))
    with pytest.raises(PermissionError, match="disabled"):
        await flags.require_task_coordination(client.metadata[COORDINATOR])


async def test_task_owner_still_needs_admin_permission(client: MagicMock) -> None:
    client.metadata[COORDINATOR]["admin_thread"] = True
    with pytest.raises(HTTPException) as error:
        await service.authorized_metadata(service.Actor(COORDINATOR, OWNER))
    assert error.value.status_code == 403


@pytest.mark.parametrize("selection", ["explicit", "auto"])
async def test_task_wakeup_preserves_current_model_selection(
    client: MagicMock, selection: str
) -> None:
    client.metadata[COORDINATOR].update(
        model="anthropic:claude-opus-5-5",
        effort="high",
        model_selection=selection,
    )

    config = await service.recipient_config(COORDINATOR)

    assert config["model_selection"] == selection
    if selection == "explicit":
        assert config["agent_model_id"] == "anthropic:claude-opus-5-5"
        assert config["agent_effort"] == "high"
    else:
        assert "agent_model_id" not in config
        assert "agent_effort" not in config


async def test_desktop_worker_cannot_be_shared(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.metadata[COORDINATOR].update(
        visibility="private",
        sandbox_id="bridge:desktop",
        sandbox_kind="bridge",
        sandbox_bridge_client="desktop",
    )
    current_task = task()
    delegation = store.TaskDelegation(
        worker_thread_id=str(uuid4()),
        task_id=current_task.id,
        coordinator_thread_id=COORDINATOR,
        instructions="Fix login",
        model=MODEL,
        effort="low",
    )
    monkeypatch.setattr(service, "record_event", AsyncMock())
    monkeypatch.setattr(TaskMessage, "deliver", AsyncMock())
    for module in (access, handlers):
        monkeypatch.setattr(module, "langgraph_client", lambda: client)

    await service.launch_worker(
        current_task, delegation, ThreadMetadata.model_validate(client.metadata[COORDINATOR])
    )

    with pytest.raises(HTTPException, match="owner's Mac") as error:
        await handlers.share_thread_with_workspace(delegation.worker_thread_id, OWNER)
    assert error.value.status_code == 409
    client.threads.update.assert_not_called()


async def test_worker_cannot_spawn_siblings(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_task = task()
    context = store.TaskContext(
        current_task,
        store.TaskMembership(thread_id=COORDINATOR, task_id=current_task.id, role="worker"),
    )
    monkeypatch.setattr(store.TaskMembership, "context_for_thread", AsyncMock(return_value=context))
    with pytest.raises(PermissionError, match="coordinator"):
        await service.spawn_worker(
            service.Actor(COORDINATOR, OWNER),
            instructions="delegate again",
            model=None,
            effort=None,
            request_id="call-1",
        )
    assert not client.created_runs


async def test_control_rejects_worker_from_another_task(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_task = task()
    context = store.TaskContext(
        current_task,
        store.TaskMembership(thread_id=COORDINATOR, task_id=current_task.id, role="coordinator"),
    )
    monkeypatch.setattr(store.TaskMembership, "context_for_thread", AsyncMock(return_value=context))
    monkeypatch.setattr(
        store.TaskDelegation,
        "get",
        AsyncMock(
            return_value=store.TaskDelegation(
                worker_thread_id="other-worker",
                task_id=uuid4(),
                coordinator_thread_id="other-coordinator",
                instructions="work",
                model=MODEL,
                effort="low",
            )
        ),
    )
    with pytest.raises(PermissionError, match="belonging to this task"):
        await service.control_worker(
            service.Actor(COORDINATOR, OWNER), worker_thread_id="other-worker", action="cancel"
        )
    client.runs.cancel_many.assert_not_awaited()


@pytest.mark.usefixtures("registry_db")
async def test_lost_launch_response_retries_same_worker_without_waiting_for_work(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        flags,
        "task_coordination_enabled",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                client.owner.typed_preferences.experimental_task_coordination
            )
        ),
    )
    actor = service.Actor(COORDINATOR, OWNER)
    workspace = WorkspaceRow(slug="engineering", name="Engineering")
    async with postgres.session() as session:
        session.add(workspace)
    client.metadata[COORDINATOR]["workspace"] = workspace.slug
    client.metadata[COORDINATOR]["title"] = "Fix login"
    assert await store.TaskMembership.context_for_thread(COORDINATOR) is None
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    client.fail_after_accept = True
    with pytest.raises(service.WorkerLaunchError) as failed:
        await asyncio.wait_for(
            service.spawn_worker(
                actor,
                instructions="Implement login fix",
                model=MODEL,
                effort="low",
                request_id="stable-call",
            ),
            timeout=5,
        )
    assert failed.value.retryable
    worker_id = failed.value.worker_thread_id
    context = await store.TaskMembership.context_for_thread(COORDINATOR)
    assert context is not None
    assert context.membership.role == "coordinator"
    assert context.task.coordinator_thread_id == COORDINATOR
    assert context.task.title == "Fix login"
    assert context.task.workspace_id == workspace.id
    assert context.task.delegated is True
    worker_context = await store.TaskMembership.context_for_thread(worker_id)
    assert worker_context is not None
    assert worker_context.task.id == context.task.id
    assert worker_context.membership.role == "worker"
    memberships = await store.sidebar_memberships([COORDINATOR, worker_id, "unrelated"])
    assert set(memberships) == {COORDINATOR, worker_id}
    assert memberships[worker_id].coordinator_thread_id == COORDINATOR
    assert memberships[worker_id].instructions == "Implement login fix"
    assert memberships[worker_id].launch_error is True
    assert await store.sidebar_memberships([COORDINATOR], workers_of=True) == {
        worker_id: memberships[worker_id]
    }
    assert client.created_runs[0]["status"] == "pending"
    delegation = await store.TaskDelegation.get(worker_id)
    assert delegation is not None and delegation.launch_error
    client.owner.preferences = {"experimental_task_coordination": False}
    with pytest.raises(PermissionError, match="disabled"):
        await service.control_worker(actor, worker_thread_id=worker_id, action="retry")
    assert len(client.created_runs) == 1
    client.owner.preferences = {"experimental_task_coordination": True}
    retried = await service.control_worker(actor, worker_thread_id=worker_id, action="retry")
    replayed = await service.spawn_worker(
        actor,
        instructions="Implement login fix",
        model=MODEL,
        effort="low",
        request_id="stable-call",
    )
    assert retried["worker_thread_id"] == replayed["worker_thread_id"] == worker_id
    assert retried["task_id"] == replayed["task_id"] == str(context.task.id)
    assert len(client.created_runs) == 1
    assert client.created_runs[0]["assistant_id"] == "agent"
    worker_config = client.created_runs[0]["config"]["configurable"]
    assert worker_config["agent_model_id"] == MODEL
    assert worker_config["agent_effort"] == "low"
    assert worker_config["model_selection"] == "explicit"
    assert worker_config["workspace"] == workspace.slug
    assert client.metadata[worker_id]["workspace"] == workspace.slug
    assert "slack_thread" not in worker_config
    assert len(await store.TaskDelegation.for_task(context.task.id)) == 1
    assert client.metadata[worker_id]["sandbox_id"] == "shared-sandbox"
    assert client.metadata[worker_id]["github_token_repositories"] == ["langchain-ai/open-swe"]
    assert "source_context" not in client.metadata[worker_id]
    async with postgres.session() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TaskMessage)
                .where(TaskMessage.thread_id == worker_id)
            )
            == 1
        )


@pytest.mark.usefixtures("registry_db")
async def test_concurrent_first_spawns_and_replay_share_one_task(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        flags,
        "task_coordination_enabled",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                client.owner.typed_preferences.experimental_task_coordination
            )
        ),
    )
    actor = service.Actor(COORDINATOR, OWNER)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    results = await asyncio.gather(
        *(
            service.spawn_worker(
                actor, instructions="Implement", model=None, effort=None, request_id=request_id
            )
            for request_id in ("first", "second", "first")
        )
    )
    assert all(result["success"] for result in results)
    assert results[0]["worker_thread_id"] == results[2]["worker_thread_id"]
    assert results[0]["worker_thread_id"] != results[1]["worker_thread_id"]
    context = await store.TaskMembership.context_for_thread(COORDINATOR)
    assert context is not None
    assert {result["task_id"] for result in results} == {str(context.task.id)}
    assert context.task.title == "Delegated work"
    assert len(await store.TaskDelegation.for_task(context.task.id)) == 2
    assert len(client.created_runs) == 2
    async with postgres.session() as session:
        assert await session.scalar(select(func.count()).select_from(store.Task)) == 1
        memberships = list(await session.scalars(select(store.TaskMembership)))
    assert {member.thread_id for member in memberships if member.role == "coordinator"} == {
        COORDINATOR
    }
    assert {member.thread_id for member in memberships if member.role == "worker"} == {
        str(result["worker_thread_id"]) for result in results
    }
    assert {member.task_id for member in memberships} == {context.task.id}


@pytest.mark.usefixtures("registry_db")
async def test_cancel_discards_owed_assignment_without_reviving_worker(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        flags,
        "task_coordination_enabled",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                client.owner.typed_preferences.experimental_task_coordination
            )
        ),
    )
    actor = service.Actor(COORDINATOR, OWNER)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    result = await service.spawn_worker(
        actor, instructions="Implement", model=None, effort=None, request_id="call"
    )
    worker_id = str(result["worker_thread_id"])
    workspace = WorkspaceRow(slug="task-cancellation", name="Task cancellation")
    async with postgres.session() as session:
        session.add(workspace)
    subscription = EventSubscription(
        thread_id=worker_id,
        workspace_id=workspace.id,
        multitask_strategy="enqueue",
        run_config={"thread_id": worker_id},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    event = EventMatch(
        thread_id=worker_id,
        subscription_id=subscription.id,
        source="github",
        delivery_id="ci-result",
        content="Build finished",
        run_config={"thread_id": worker_id},
    )
    async with postgres.session() as session:
        session.add(subscription)
        await event.record(session)
    client.owner.preferences = {"experimental_task_coordination": False}
    cancelled = await service.control_worker(actor, worker_thread_id=worker_id, action="cancel")
    assert cancelled["cancellation_requested"] is True
    assert await EventMatch.owed(worker_id, []) == []
    assert await EventSubscription.for_thread(worker_id) == []
    assert await EventMatch.deliver(worker_id, "enqueue") is False
    assert await TaskMessage.deliver(worker_id, "interrupt") is False
    assert client.created_runs[0]["status"] == "interrupted"
    with pytest.raises(ValueError, match="uncancelled"):
        await service.control_worker(actor, worker_thread_id=worker_id, action="retry")
    assert len(client.created_runs) == 1


@pytest.mark.usefixtures("registry_db")
async def test_finished_worker_can_receive_follow_up_and_gain_a_sibling(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        flags,
        "task_coordination_enabled",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                client.owner.typed_preferences.experimental_task_coordination
            )
        ),
    )
    actor = service.Actor(COORDINATOR, OWNER)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    first = await service.spawn_worker(
        actor, instructions="Implement login fix", model=None, effort=None, request_id="first"
    )
    worker_id = str(first["worker_thread_id"])
    initial_messages = TaskMessage.messages(await TaskMessage.owed(worker_id, []))
    client.threads.get_state.return_value = {"values": {"messages": initial_messages}}
    client.created_runs[0]["status"] = "success"
    client.statuses[worker_id] = "idle"

    status = await service.task_status(actor)
    workers = status["workers"]
    assert isinstance(workers, list)
    (worker,) = workers
    assert status["task_id"] == first["task_id"]
    assert worker["worker_thread_id"] == worker_id
    assert worker["status"] == "idle"
    assert worker["latest_run"]["status"] == "success"

    sent = await service.message_task_thread(
        actor,
        message="Check logout too",
        worker_thread_id=worker_id,
        request_id="follow-up",
    )
    assert sent["success"] is True
    assert sent["recipient_thread_id"] == worker_id
    (follow_up,) = await TaskMessage.owed(worker_id, initial_messages)
    assert "Check logout too" in follow_up.content
    follow_up_display = TaskEventMetadata.model_validate(follow_up.task_event)
    assert follow_up_display.sender_role == "coordinator"
    assert str(follow_up_display.sender_thread_id) == COORDINATOR
    assert follow_up_display.content == "Check logout too"
    assert follow_up_display.kind == "message"
    assert follow_up_display.status is None
    assert len(client.created_runs) == 2
    assert client.created_runs[-1]["thread_id"] == worker_id
    assert client.created_runs[-1]["status"] == "pending"

    sibling = await service.spawn_worker(
        actor, instructions="Review login fix", model=None, effort=None, request_id="sibling"
    )
    assert sibling["success"] is True
    assert sibling["task_id"] == first["task_id"]
    assert sibling["worker_thread_id"] != worker_id
    assert len(client.created_runs) == 3
    context = await store.TaskMembership.context_for_thread(worker_id)
    assert context is not None
    assert str(context.task.id) == first["task_id"]
    assert context.membership.role == "worker"
    assert len(await store.TaskDelegation.for_task(context.task.id)) == 2

    client.owner.preferences = {"experimental_task_coordination": False}
    with pytest.raises(PermissionError, match="disabled"):
        await service.spawn_worker(
            actor, instructions="Another worker", model=None, effort=None, request_id="disabled"
        )
    client.statuses[COORDINATOR] = "busy"
    senders = [worker_id, str(sibling["worker_thread_id"])]
    reports = ['Blocked on "login" & <schema>; can you help?', "Checked:\n```python\na < b\n```"]
    for sender, report in zip(senders, reports, strict=True):
        await service.message_task_thread(
            service.Actor(sender, OWNER),
            message=report,
            worker_thread_id=None,
            request_id="report",
        )
    assert len(client.created_runs) == 3
    owed = await TaskMessage.owed(COORDINATOR, [])
    displays = [TaskEventMetadata.model_validate(event.task_event) for event in owed]
    assert [str(display.sender_thread_id) for display in displays] == senders
    assert [display.content for display in displays] == reports
    assert all(display.sender_label is None for display in displays)
    assert all(display.kind == "message" and display.status is None for display in displays)
    message_ids = [f"event-match:{event.id}" for event in owed]
    client.statuses[COORDINATOR] = "idle"
    assert await TaskMessage.deliver(COORDINATOR, "enqueue")
    assert len(client.created_runs) == 4
    wake = client.created_runs[-1]
    assert wake["thread_id"] == COORDINATOR
    delivered_ids = [message["id"] for message in wake["input"]["messages"] if "id" in message]
    assert delivered_ids == message_ids
