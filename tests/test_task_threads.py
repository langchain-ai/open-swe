import asyncio
from collections.abc import Mapping
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy import func, select

from agent.database import postgres
from agent.tasks import service, store
from agent.threads import creation
from agent.webhooks import event_matches
from agent.webhooks.event_matches import EventMatch

MODEL = "openai:gpt-6-astra"
COORDINATOR = str(uuid4())
OWNER = "owner"


def task() -> store.CoordinatedTask:
    return store.CoordinatedTask(
        coordinator_thread_id=COORDINATOR,
        title="Fix login",
        acceptance_criteria=["Login succeeds", "Regression check passes"],
        workspace="default",
    )


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
    client.threads.get_state = AsyncMock(return_value={"values": {"messages": []}})
    client.runs.create = AsyncMock(side_effect=run_create)
    client.runs.list = AsyncMock(side_effect=list_runs)
    client.runs.cancel_many = AsyncMock(side_effect=cancel_many)
    monkeypatch.setattr(service, "langgraph_client", lambda: client)
    monkeypatch.setattr(event_matches, "dispatch_client", lambda: client)
    monkeypatch.setattr(service, "enforce_github_login_gate", AsyncMock())
    monkeypatch.setattr(service, "get_profile", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "resolve_run_email", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "COMPLETION_WEBHOOK_URL", "https://example.test/completion")
    monkeypatch.setattr(creation, "_owner_prefers_tools_in_sandbox", AsyncMock(return_value=False))
    from agent import dispatch
    from agent.slack import thinking

    monkeypatch.setattr(dispatch, "_run_user_id", AsyncMock(return_value=None))
    monkeypatch.setattr(thinking, "sync_slack_background_status", AsyncMock())
    monkeypatch.setattr(service, "interrupt_transcript_turns", AsyncMock())
    return client


async def test_public_thread_does_not_allow_using_owners_credentials(client: MagicMock) -> None:
    with pytest.raises(PermissionError, match="thread owner"):
        await service.authorized_metadata(service.Actor(COORDINATOR, "other-user"))


async def test_task_owner_still_needs_admin_permission(client: MagicMock) -> None:
    client.metadata[COORDINATOR]["admin_thread"] = True
    with pytest.raises(HTTPException) as error:
        await service.authorized_metadata(service.Actor(COORDINATOR, OWNER))
    assert error.value.status_code == 403


async def test_worker_cannot_spawn_siblings(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_task = task()
    context = store.TaskContext(
        current_task,
        store.TaskMembership(thread_id=COORDINATOR, task_id=current_task.id, role="worker"),
    )
    monkeypatch.setattr(store, "load_context", AsyncMock(return_value=context))
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
    monkeypatch.setattr(store, "load_context", AsyncMock(return_value=context))
    monkeypatch.setattr(
        store,
        "get_delegation",
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


async def test_notification_does_not_hide_failure_to_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service, "record_event", AsyncMock(side_effect=ConnectionError("database down"))
    )
    wake = AsyncMock()
    monkeypatch.setattr(EventMatch, "deliver", wake)
    with pytest.raises(ConnectionError, match="database down"):
        await service.notify(task(), COORDINATOR, "finished:worker:run", "result")
    wake.assert_not_awaited()


@pytest.mark.usefixtures("registry_db")
async def test_lost_launch_response_retries_same_worker_without_waiting_for_work(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = service.Actor(COORDINATOR, OWNER)
    configured = await service.configure_task(
        actor, title="Fix login", acceptance_criteria=["Login succeeds"]
    )
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    client.fail_after_accept = True
    result = await asyncio.wait_for(
        service.spawn_worker(
            actor,
            instructions="Implement login fix",
            model=MODEL,
            effort="low",
            request_id="stable-call",
        ),
        timeout=5,
    )
    assert result["success"] is False
    worker_id = str(result["worker_thread_id"])
    assert client.created_runs[0]["status"] == "pending"
    assert (await store.get_delegation(worker_id)).launch_error
    retried = await service.control_worker(actor, worker_thread_id=worker_id, action="retry")
    replayed = await service.spawn_worker(
        actor,
        instructions="Implement login fix",
        model=MODEL,
        effort="low",
        request_id="stable-call",
    )
    assert retried["worker_thread_id"] == replayed["worker_thread_id"] == worker_id
    assert len(client.created_runs) == 1
    assert client.created_runs[0]["assistant_id"] == "agent"
    worker_config = client.created_runs[0]["config"]["configurable"]
    assert worker_config["agent_model_id"] == MODEL
    assert worker_config["agent_effort"] == "low"
    assert worker_config["model_selection"] == "explicit"
    assert "slack_thread" not in worker_config
    assert len(await store.list_delegations(configured.id)) == 1
    assert client.metadata[worker_id]["sandbox_id"] == "shared-sandbox"
    assert client.metadata[worker_id]["github_token_repositories"] == ["langchain-ai/open-swe"]
    assert "source_context" not in client.metadata[worker_id]
    assert (await store.load_context(COORDINATOR)).task.status == "active"
    async with postgres.session() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EventMatch)
                .where(EventMatch.thread_id == worker_id)
            )
            == 1
        )


@pytest.mark.usefixtures("registry_db")
async def test_cancel_discards_owed_assignment_without_reviving_worker(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = service.Actor(COORDINATOR, OWNER)
    await service.configure_task(actor, title="Fix login", acceptance_criteria=["Login succeeds"])
    monkeypatch.setattr(service, "model_choice", AsyncMock(return_value=(MODEL, "low")))
    result = await service.spawn_worker(
        actor, instructions="Implement", model=None, effort=None, request_id="call"
    )
    worker_id = str(result["worker_thread_id"])
    cancelled = await service.control_worker(actor, worker_thread_id=worker_id, action="cancel")
    assert cancelled["cancellation_requested"] is True
    assert await EventMatch.owed(worker_id, []) == []
    assert await EventMatch.deliver(worker_id, "enqueue") is False
    assert client.created_runs[0]["status"] == "interrupted"
    with pytest.raises(ValueError, match="uncancelled"):
        await service.control_worker(actor, worker_thread_id=worker_id, action="retry")
    assert len(client.created_runs) == 1


@pytest.mark.usefixtures("registry_db")
async def test_assessment_covers_criteria_and_is_invalidated_by_task_edits(
    client: MagicMock,
) -> None:
    actor = service.Actor(COORDINATOR, OWNER)
    configured = await service.configure_task(
        actor, title="Fix login", acceptance_criteria=["Login succeeds", "Regression passes"]
    )
    with pytest.raises(ValueError, match="each acceptance criterion"):
        await service.assess_task(
            actor, evidence=["works"], completed=True, revision=configured.revision
        )
    completed = await service.assess_task(
        actor,
        evidence=["Fixed in commit abc", "Focused test passes"],
        completed=True,
        revision=configured.revision,
    )
    assert completed.status == "completed"
    revised = await service.configure_task(
        actor,
        title="Fix login",
        acceptance_criteria=["Login succeeds", "Regression passes", "No error on logout"],
    )
    assert revised.status == "active"
    assert revised.assessment == []
    assert revised.id == completed.id
    with pytest.raises(ValueError, match="task changed"):
        await service.assess_task(
            actor, evidence=["done", "done", "done"], completed=True, revision=completed.revision
        )
