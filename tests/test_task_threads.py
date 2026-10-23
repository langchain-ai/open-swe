import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy import func, select, update

from openswe.bridge.store import BridgeStore
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.database import postgres
from openswe.tasks import messages as task_messages
from openswe.tasks import presentation, service, store
from openswe.tasks.messages import TaskMessage
from openswe.tasks.presentation import TaskEventMetadata
from openswe.tasks.schemas import ThreadMetadata
from openswe.threads import access, handlers
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
Launch = Callable[[service.Actor, str, str], Awaitable[dict[str, object]]]


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

    async def list_runs(
        thread_id: str, *, limit: int | None = None, offset: int = 0, status: str | None = None
    ) -> list[dict[str, object]]:
        runs = [
            run
            for run in reversed(client.created_runs)
            if run["thread_id"] == thread_id and (status is None or run["status"] == status)
        ]
        return runs[offset:][:limit]

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
    owner = User(identities=[UserIdentity(provider="github", external_id="1", login=OWNER)])
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
    from openswe import dispatch
    from openswe.slack import thinking

    monkeypatch.setattr(
        dispatch, "_run_user_metadata", AsyncMock(return_value={"ls_repo_provider": "github"})
    )
    monkeypatch.setattr(thinking, "sync_slack_background_status", AsyncMock())
    monkeypatch.setattr(service, "interrupt_transcript_turns", AsyncMock())
    monkeypatch.setattr(service, "cancel_thread_wakeups", AsyncMock())
    return client


@pytest.fixture
def launch(monkeypatch: pytest.MonkeyPatch) -> Launch:
    """Launch a worker as a coordinator's MDA async subagent does, one thread per tool call."""
    parents: dict[str, str] = {}

    async def mda_thread(thread_id: str) -> dict[str, object]:
        return {
            "metadata": {"subagent_name": "general-purpose", "parent_thread_id": parents[thread_id]}
        }

    runtimes = MagicMock()
    runtimes.mda.get = AsyncMock(side_effect=mda_thread)
    monkeypatch.setattr(service, "RUNTIMES", runtimes)

    async def launch(actor: service.Actor, request_id: str, instructions: str) -> dict[str, object]:
        launched = await service.authorize_async_worker(actor, request_id, instructions)
        if launched is not None:
            return launched
        worker_thread_id = str(uuid5(NAMESPACE_URL, f"{actor.thread_id}:{request_id}"))
        parents[worker_thread_id] = actor.thread_id
        return await service.register_async_worker(
            actor, request_id, worker_thread_id, instructions
        )

    return launch


@pytest.mark.parametrize("connected", [True, False])
async def test_cli_bridge_delegates_only_while_it_is_served(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, connected: bool
) -> None:
    client.metadata[COORDINATOR].update(sandbox_id="bridge:cli", sandbox_bridge_client="cli")
    monkeypatch.setattr(store.TaskMembership, "context_for_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(BridgeStore, "is_connected", AsyncMock(return_value=connected))
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    actor = service.Actor(COORDINATOR, OWNER)

    if connected:
        assert (await service.Delegation.authorize(actor)).metadata.sandbox_id == "bridge:cli"
    else:
        with pytest.raises(ValueError, match="connected"):
            await service.Delegation.authorize(actor)


async def test_public_thread_does_not_allow_using_owners_credentials(client: MagicMock) -> None:
    with pytest.raises(PermissionError, match="thread owner"):
        await service.authorized_metadata(service.Actor(COORDINATOR, "other-user"))


async def test_task_owner_id_survives_rename_and_rejects_reassigned_login(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = await service.authorized_metadata(service.Actor(COORDINATOR, OWNER))
    owner_id = metadata.owner_user_id
    assert owner_id is not None
    assert client.metadata[COORDINATOR]["owner_user_id"] == str(owner_id)
    owner = User(
        id=owner_id,
        identities=[UserIdentity(provider="github", external_id="1", login="renamed-owner")],
    )
    replacement = User(identities=[UserIdentity(provider="github", external_id="2", login=OWNER)])
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
    with pytest.raises(PermissionError, match="thread owner"):
        await service.authorized_metadata(service.Actor(COORDINATOR, OWNER))


@pytest.mark.usefixtures("registry_db")
async def test_system_thread_delegates_to_system_owned_workers(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, launch: Launch
) -> None:
    client.metadata[COORDINATOR] = {
        "owner_type": "system",
        "visibility": "public",
        "source": "schedule",
        "schedule_id": "nightly",
        "workspace": "default",
        "sandbox_id": "shared-sandbox",
    }
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    actor = service.Actor(COORDINATOR)
    # A scheduled run acts for no person; a Slack post's author must not become the owner.
    worker_id, posted = [
        str((await launch(launcher, call, "Implement"))["worker_thread_id"])
        for launcher, call in ((actor, "scheduled"), (service.Actor(COORDINATOR, OWNER), "posted"))
    ]
    for worker in (client.metadata[worker_id], client.metadata[posted]):
        assert worker["owner_type"] == "system"
        assert "owner_user_id" not in worker and "owner_login" not in worker

    sent = await service.message_task_thread(
        actor, message="Check logout too", worker_thread_id=worker_id, request_id="follow-up"
    )
    assert sent == {"success": True, "recipient_thread_id": worker_id}
    (follow_up,) = client.created_runs
    assert "github_login" not in follow_up["config"]["configurable"]
    reply = await service.message_task_thread(
        service.Actor(worker_id), message="Done", worker_thread_id=None, request_id="report"
    )
    assert reply["recipient_thread_id"] == COORDINATOR

    outsider = str(uuid4())
    client.metadata[outsider] = {"owner_type": "system", "visibility": "public"}
    with pytest.raises(PermissionError, match="its own task"):
        await service.authorized_metadata(service.Actor(outsider), worker_id)
    client.metadata[outsider] = {"owner_type": "user", "owner_login": OWNER}
    with pytest.raises(PermissionError, match="share their coordinator's owner"):
        await service.authorized_metadata(service.Actor(outsider, OWNER), worker_id)

    cancelled = await service.control_worker(actor, worker_thread_id=worker_id, action="cancel")
    assert cancelled["cancellation_requested"] is True
    assert follow_up["status"] == "interrupted"


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
    for module in (access, handlers):
        monkeypatch.setattr(module, "langgraph_client", lambda: client)

    await service.index_worker(
        current_task, delegation, ThreadMetadata.model_validate(client.metadata[COORDINATOR])
    )

    with pytest.raises(HTTPException, match="owner's machine") as error:
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
        await service.authorize_async_worker(
            service.Actor(COORDINATOR, OWNER), "call-1", "delegate again"
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
async def test_concurrent_first_launches_and_replay_share_one_task(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, launch: Launch
) -> None:
    actor = service.Actor(COORDINATOR, OWNER)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    results = await asyncio.gather(
        *(launch(actor, request_id, "Implement") for request_id in ("first", "second", "first"))
    )
    assert all(result["success"] for result in results)
    assert results[0]["worker_thread_id"] == results[2]["worker_thread_id"]
    assert results[0]["worker_thread_id"] != results[1]["worker_thread_id"]
    context = await store.TaskMembership.context_for_thread(COORDINATOR)
    assert context is not None
    assert {result["task_id"] for result in results} == {str(context.task.id)}
    assert context.task.title == "Delegated work"
    assert len(await store.TaskDelegation.for_task(context.task.id)) == 2
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
async def test_cancel_discards_owed_events_without_reviving_worker(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, launch: Launch
) -> None:
    actor = service.Actor(COORDINATOR, OWNER)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    result = await launch(actor, "call", "Implement")
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
    cancelled = await service.control_worker(actor, worker_thread_id=worker_id, action="cancel")
    assert cancelled["cancellation_requested"] is True
    assert await EventMatch.owed(worker_id, []) == []
    assert await EventSubscription.for_thread(worker_id) == []
    assert await EventMatch.deliver(worker_id, "enqueue") is False
    assert await TaskMessage.deliver(worker_id, "interrupt") is False
    assert not client.created_runs

    # Unlike a person stopping the task, the coordinator that cancelled still hears the outcome.
    from openswe.tasks.events import finished_delivery_id, worker_finished

    run_id = str(uuid4())
    await worker_finished(worker_id, run_id, "interrupted", {})
    assert await TaskMessage.recorded(COORDINATOR, finished_delivery_id(worker_id, run_id))


@pytest.mark.usefixtures("registry_db")
async def test_finished_worker_can_receive_follow_up_and_gain_a_sibling(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, launch: Launch
) -> None:
    actor = service.Actor(COORDINATOR, OWNER)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    first = await launch(actor, "first", "Implement login fix")
    worker_id = str(first["worker_thread_id"])
    client.statuses[worker_id] = "idle"

    status = await service.task_status(actor)
    workers = status["workers"]
    assert isinstance(workers, list)
    (worker,) = workers
    assert status["task_id"] == first["task_id"]
    assert worker["worker_thread_id"] == worker_id
    assert worker["status"] == "idle"

    sent = await service.message_task_thread(
        actor,
        message="Check logout too",
        worker_thread_id=worker_id,
        request_id="follow-up",
    )
    assert sent["success"] is True
    assert sent["recipient_thread_id"] == worker_id
    (follow_up,) = await TaskMessage.owed(worker_id, [])
    assert "Check logout too" in follow_up.content
    follow_up_display = TaskEventMetadata.model_validate(follow_up.task_event)
    assert follow_up_display.sender_role == "coordinator"
    assert str(follow_up_display.sender_thread_id) == COORDINATOR
    assert follow_up_display.content == "Check logout too"
    assert follow_up_display.kind == "message"
    assert follow_up_display.status is None
    (follow_up_run,) = client.created_runs
    assert follow_up_run["thread_id"] == worker_id
    assert follow_up_run["status"] == "pending"

    sibling = await launch(actor, "sibling", "Review login fix")
    assert sibling["success"] is True
    assert sibling["task_id"] == first["task_id"]
    assert sibling["worker_thread_id"] != worker_id
    context = await store.TaskMembership.context_for_thread(worker_id)
    assert context is not None
    assert str(context.task.id) == first["task_id"]
    assert context.membership.role == "worker"
    assert len(await store.TaskDelegation.for_task(context.task.id)) == 2

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
    assert len(client.created_runs) == 1
    owed = await TaskMessage.owed(COORDINATOR, [])
    displays = [TaskEventMetadata.model_validate(event.task_event) for event in owed]
    assert [str(display.sender_thread_id) for display in displays] == senders
    assert [display.content for display in displays] == reports
    assert all(display.sender_label is None for display in displays)
    assert all(display.kind == "message" and display.status is None for display in displays)
    message_ids = [f"event-match:{event.id}" for event in owed]
    client.statuses[COORDINATOR] = "idle"
    assert await TaskMessage.deliver(COORDINATOR, "enqueue")
    assert len(client.created_runs) == 2
    wake = client.created_runs[-1]
    assert wake["thread_id"] == COORDINATOR
    delivered_ids = [message["id"] for message in wake["input"]["messages"] if "id" in message]
    assert delivered_ids == message_ids


@pytest.mark.usefixtures("registry_db")
async def test_coordinator_settles_once_every_worker_report_has_reached_it(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, launch: Launch
) -> None:
    from openswe.tasks import activity
    from openswe.tasks.events import worker_finished

    monkeypatch.setattr(activity, "langgraph_client", lambda: client)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    actor = service.Actor(COORDINATOR, OWNER)
    assert (await activity.CoordinatorActivity.of(COORDINATOR)).settled

    worker_id = str((await launch(actor, "call", "Implement"))["worker_thread_id"])
    run_id = str(uuid4())
    run: dict[str, object] = {"thread_id": worker_id, "run_id": run_id, "status": "running"}
    client.created_runs.append(run)

    async def states() -> list[str]:
        current = await activity.CoordinatorActivity.of(COORDINATOR)
        assert current.settled is not any(worker.active for worker in current.workers)
        return [worker.state for worker in current.workers]

    assert await states() == ["running"]
    run.update(status="success", updated_at=datetime.now(UTC).isoformat())
    assert await states() == ["reporting"]

    answer = {"values": {"messages": [{"type": "ai", "content": "Implemented"}]}}
    await worker_finished(worker_id, run_id, "success", answer)
    (wake,) = [run for run in client.created_runs if run["thread_id"] == COORDINATOR]
    current = await activity.CoordinatorActivity.of(COORDINATOR)
    assert [worker.state for worker in current.workers] == ["done"]
    assert current.runs == [wake["run_id"]]
    assert current.running
    assert not current.settled

    wake["status"] = "success"
    assert not (await activity.CoordinatorActivity.of(COORDINATOR)).settled
    client.threads.get_state.return_value = {"values": {"messages": wake["input"]["messages"]}}
    assert (await activity.CoordinatorActivity.of(COORDINATOR)).settled

    stuck = str((await launch(actor, "stuck", "Review"))["worker_thread_id"])
    assert sorted(await states()) == ["done", "starting"]
    async with postgres.session() as session:
        delegation = await session.get_one(store.TaskDelegation, stuck)
        delegation.created_at = datetime.now(UTC) - activity.LOST_AFTER
    current = await activity.CoordinatorActivity.of(COORDINATOR)
    assert {
        worker.worker_thread_id: (worker.state, worker.error) for worker in current.workers
    } == {
        worker_id: ("done", None),
        stuck: ("failed", "The worker did not start within 5 minutes."),
    }
    assert current.settled


@pytest.mark.usefixtures("registry_db")
async def test_report_held_through_a_long_coordinator_run_is_awaited_once_it_ends(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, launch: Launch
) -> None:
    from openswe.tasks import activity
    from openswe.tasks.events import worker_finished

    monkeypatch.setattr(activity, "langgraph_client", lambda: client)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    first = str(uuid4())
    coordinating: dict[str, object] = {
        "thread_id": COORDINATOR,
        "run_id": first,
        "status": "running",
    }
    client.created_runs.append(coordinating)
    client.statuses[COORDINATOR] = "busy"
    worker_id = str(
        (await launch(service.Actor(COORDINATOR, OWNER), "call", "Implement"))["worker_thread_id"]
    )
    worker_run = str(uuid4())
    long_ago = datetime.now(UTC) - 2 * activity.LOST_AFTER
    client.created_runs.append(
        {
            "thread_id": worker_id,
            "run_id": worker_run,
            "status": "success",
            "updated_at": long_ago.isoformat(),
        }
    )
    answer = {"values": {"messages": [{"type": "ai", "content": "Implemented"}]}}
    await worker_finished(worker_id, worker_run, "success", answer)
    async with postgres.session() as session:
        await session.execute(update(TaskMessage).values(matched_at=long_ago))

    current = await activity.CoordinatorActivity.of(COORDINATOR, after=first)
    assert (current.runs, current.running, current.settled) == ([], True, False)
    coordinating.update(status="success", updated_at=datetime.now(UTC).isoformat())
    client.statuses[COORDINATOR] = "idle"
    current = await activity.CoordinatorActivity.of(COORDINATOR, after=first)
    assert current.awaiting_delivery
    assert not current.settled
    coordinating["updated_at"] = long_ago.isoformat()
    assert (await activity.CoordinatorActivity.of(COORDINATOR, after=first)).settled


@pytest.mark.usefixtures("registry_db")
async def test_cancelling_a_coordinators_workers_spares_finished_ones(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, launch: Launch
) -> None:
    from openswe.tasks import activity
    from openswe.tasks.events import finished_delivery_id, worker_finished

    monkeypatch.setattr(activity, "langgraph_client", lambda: client)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    actor = service.Actor(COORDINATOR, OWNER)
    running = str((await launch(actor, "running", "Implement"))["worker_thread_id"])
    finished = str((await launch(actor, "finished", "Review"))["worker_thread_id"])
    running_run, finished_run = str(uuid4()), str(uuid4())
    client.created_runs += [
        {"thread_id": running, "run_id": running_run, "status": "running"},
        {"thread_id": finished, "run_id": finished_run, "status": "success"},
    ]
    client.statuses[COORDINATOR] = "busy"
    await service.message_task_thread(
        service.Actor(running, OWNER), message="Halfway", worker_thread_id=None, request_id="note"
    )
    answer = {"values": {"messages": [{"type": "ai", "content": "Reviewed"}]}}
    await worker_finished(finished, finished_run, "success", answer)

    await (await activity.CoordinatorActivity.of(COORDINATOR)).stop_workers()
    # The interrupted run's completion lands after the stop, with the coordinator idle.
    client.statuses[COORDINATOR] = "idle"
    await worker_finished(running, running_run, "interrupted", {})

    owed = await TaskMessage.owed(COORDINATOR, [])
    assert [message.delivery_id for message in owed] == [
        finished_delivery_id(finished, finished_run)
    ]
    assert not [run for run in client.created_runs if run["thread_id"] == COORDINATOR]
    current = await activity.CoordinatorActivity.of(COORDINATOR)
    assert {worker.worker_thread_id: worker.state for worker in current.workers} == {
        running: "cancelled",
        finished: "done",
    }


@pytest.mark.usefixtures("registry_db")
async def test_async_subagent_becomes_one_ordinary_worker_of_its_coordinators_task(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openswe.agent_api.client import MDA_GRAPH_METADATA_KEY, MDA_WORKER_GRAPH_ID
    from openswe.tasks.events import worker_finished

    actor = service.Actor(COORDINATOR, OWNER)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    worker, replayed, stranger = str(uuid4()), str(uuid4()), str(uuid4())
    launched = {
        worker: COORDINATOR,
        replayed: COORDINATOR,
        stranger: str(uuid4()),
    }

    async def mda_thread(thread_id: str) -> dict[str, object] | None:
        parent = launched.get(thread_id)
        return {"metadata": {"subagent_name": "general-purpose", "parent_thread_id": parent}}

    runtimes = MagicMock()
    runtimes.mda.get = AsyncMock(side_effect=mda_thread)
    monkeypatch.setattr(service, "RUNTIMES", runtimes)

    with pytest.raises(ValueError, match="instructions"):
        await service.authorize_async_worker(actor, "blank", "  ")
    assert await service.authorize_async_worker(actor, "launch", "Implement login fix") is None
    first = await service.register_async_worker(actor, "launch", worker, "Implement login fix")
    # The coordinator's registration and the worker's own adoption race; both land once.
    again = await service.register_async_worker(actor, "launch", worker, "Implement login fix")
    assert again == first
    # A replayed launch reuses its worker instead of launching another.
    assert await service.authorize_async_worker(actor, "launch", "Implement login fix") == first
    with pytest.raises(store.WorkerAlreadyLaunchedError) as replay:
        await service.register_async_worker(actor, "launch", replayed, "Implement login fix")
    assert service.worker_reply(replay.value.task, replay.value.delegation) == first
    with pytest.raises(PermissionError, match="not an async worker this coordinator launched"):
        await service.register_async_worker(actor, "elsewhere", stranger, "Implement")

    context = await store.TaskMembership.context_for_thread(worker)
    assert context is not None
    assert str(context.task.id) == first["task_id"]
    assert context.membership.role == "worker"
    assert len(await store.TaskDelegation.for_task(context.task.id)) == 1
    metadata = client.metadata[worker]
    assert metadata["sandbox_host_thread_id"] == COORDINATOR
    assert metadata["sandbox_id"] == "shared-sandbox"
    assert metadata["owner_login"] == OWNER
    assert metadata[MDA_GRAPH_METADATA_KEY] == MDA_WORKER_GRAPH_ID
    # MDA started the assignment; Open SWE queued nothing to start it again.
    assert client.created_runs == []
    assert await TaskMessage.owed(worker, []) == []

    # A worker launches no workers of its own.
    with pytest.raises(PermissionError, match="coordinator"):
        await service.authorize_async_worker(service.Actor(worker, OWNER), "nested", "Help")

    client.statuses[COORDINATOR] = "busy"
    payload = {"values": {"messages": [{"type": "ai", "content": "Fixed login."}]}}
    assert await worker_finished(worker, "run-1", "success", payload)
    # Settled twice (two replicas, or a retried settle): reported once.
    assert await worker_finished(worker, "run-1", "success", payload)
    (report,) = await TaskMessage.owed(COORDINATOR, [])
    assert "Fixed login." in report.content
    # A busy coordinator picks the report up before its next model call; an idle one wakes.
    assert not [run for run in client.created_runs if run["thread_id"] == COORDINATOR]
    client.statuses[COORDINATOR] = "idle"
    assert await TaskMessage.deliver(COORDINATOR, "enqueue")
    (wake,) = [run for run in client.created_runs if run["thread_id"] == COORDINATOR]
    assert [m["id"] for m in wake["input"]["messages"] if "id" in m] == [f"event-match:{report.id}"]

    sent = await service.message_task_thread(
        actor, message="Check logout too", worker_thread_id=worker, request_id="follow-up"
    )
    assert sent == {"success": True, "recipient_thread_id": worker}
    (follow_up,) = [run for run in client.created_runs if run["thread_id"] == worker]

    cancelled = await service.control_worker(actor, worker_thread_id=worker, action="cancel")
    assert cancelled["cancellation_requested"] is True
    assert follow_up["status"] == "interrupted"
    with pytest.raises(ValueError, match="cancelled"):
        await service.message_task_thread(
            actor, message="One more thing", worker_thread_id=worker, request_id="late"
        )
    assert await TaskMessage.deliver(worker, "enqueue") is False
    with pytest.raises(ValueError, match="cancelled"):
        await service.register_async_worker(actor, "launch", worker, "Implement login fix")


@pytest.mark.usefixtures("registry_db")
async def test_concurrent_registrations_of_one_launch_make_one_worker(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A replayed launch started a second thread before the first registered; their
    # registrations race in different backend processes.
    actor = service.Actor(COORDINATOR, OWNER)
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    threads = [str(uuid4()), str(uuid4())]
    runtimes = MagicMock()
    runtimes.mda.get = AsyncMock(
        return_value={
            "metadata": {"subagent_name": "general-purpose", "parent_thread_id": COORDINATOR}
        }
    )
    monkeypatch.setattr(service, "RUNTIMES", runtimes)

    results = await asyncio.gather(
        *(
            service.register_async_worker(actor, "launch", thread, "Implement login fix")
            for thread in threads
        ),
        return_exceptions=True,
    )

    registered = [result for result in results if isinstance(result, dict)]
    assert len(registered) == 1
    assert [type(result) for result in results if isinstance(result, BaseException)] == [
        store.WorkerAlreadyLaunchedError
    ]
    context = await store.TaskMembership.context_for_thread(COORDINATOR)
    assert context is not None
    (delegation,) = await store.TaskDelegation.for_task(context.task.id)
    assert delegation.worker_thread_id == registered[0]["worker_thread_id"]
    replayed = await service.authorize_async_worker(actor, "launch", "Implement login fix")
    assert replayed == registered[0]
