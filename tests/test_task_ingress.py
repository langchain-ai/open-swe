import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent import dispatch
from agent.openai_responses.conversations import SandboxCaller
from agent.sandboxes import tool_routes, tool_runtime
from agent.sandboxes.tool_access import ToolAccess
from agent.sandboxes.tool_models import ToolArguments
from agent.tasks import ingress, store
from agent.tasks.policy import TaskPermissionError
from agent.threads import handlers, proxy, runs


@pytest.fixture
def worker_task(monkeypatch: pytest.MonkeyPatch) -> store.TaskRecord:
    task = store.TaskRecord(
        id="task-1",
        coordinator_thread_id="coordinator",
        workspace="default",
        title="Repair login",
        acceptance_criteria=["Login succeeds"],
        delegated=True,
        status="active",
        completion_evidence=None,
    )

    async def task_for_thread(thread_id: str) -> store.TaskRecord | None:
        return task if thread_id in {"worker", "coordinator"} else None

    async def membership_for_thread(thread_id: str) -> store.Membership | None:
        if thread_id not in {"worker", "coordinator"}:
            return None
        return store.Membership(
            task_id=task.id,
            thread_id=thread_id,
            role="worker" if thread_id == "worker" else "coordinator",
        )

    monkeypatch.setattr(store.postgres, "configured", lambda: False)
    monkeypatch.setattr(store, "task_for_thread", task_for_thread)
    monkeypatch.setattr(store, "membership_for_thread", membership_for_thread)
    return task


async def test_dispatch_rejects_worker_before_any_side_effect(
    worker_task: store.TaskRecord,
) -> None:
    client = SimpleNamespace(threads=AsyncMock(), runs=AsyncMock())
    with pytest.raises(TaskPermissionError, match="coordinator thread coordinator"):
        await dispatch.create_durable_run(
            "worker",
            "agent",
            input={"messages": []},
            source="task_worker",
            thread_title="Forged coordinator",
            config={"configurable": {"task_worker_dispatch": True, "task_role": "coordinator"}},
            metadata={"task_worker_dispatch": True, "task_role": "coordinator"},
            client=client,
        )
    assert client.threads.mock_calls == []
    assert client.runs.mock_calls == []


async def test_trusted_worker_dispatch_enqueues_independent_run(
    monkeypatch: pytest.MonkeyPatch, worker_task: store.TaskRecord
) -> None:
    client = SimpleNamespace(threads=AsyncMock(), runs=AsyncMock())
    client.runs.create.return_value = {"run_id": "worker-run"}
    monkeypatch.setattr(dispatch, "_run_user_id", AsyncMock(return_value=None))
    result = await dispatch.create_durable_run(
        "worker",
        "agent",
        input={"messages": [{"role": "user", "content": "Fix login"}]},
        source="task_worker",
        thread_title=None,
        config={"configurable": {"slack_ask": True}},
        client=client,
        multitask_strategy="enqueue",
        task_worker_dispatch=True,
    )
    assert result["run_id"] == "worker-run"
    assert client.runs.create.call_args.args == ("worker", "agent")
    assert client.runs.create.call_args.kwargs["multitask_strategy"] == "enqueue"
    assert "task_worker_dispatch" not in client.runs.create.call_args.kwargs["config"]


@pytest.mark.parametrize("method", ["run.start", "input.respond", "input.inject"])
async def test_worker_commands_rejected_before_busy_steering_or_proxy(
    monkeypatch: pytest.MonkeyPatch, worker_task: store.TaskRecord, method: str
) -> None:
    client = SimpleNamespace(threads=AsyncMock())
    client.threads.get.return_value = {
        "thread_id": "worker",
        "status": "busy",
        "metadata": {"owner_login": "mason", "visibility": "private", "source": "dashboard"},
    }
    monkeypatch.setattr(proxy, "langgraph_client", lambda: client)
    steer = AsyncMock()
    enqueue = AsyncMock()
    monkeypatch.setattr(proxy, "steer_running_thread", steer)
    monkeypatch.setattr(proxy, "queue_follow_up_run", enqueue)
    with pytest.raises(HTTPException) as error:
        await proxy.proxy_dashboard_thread_commands(
            "worker", "mason", json.dumps({"method": method, "params": {}}).encode()
        )
    assert error.value.status_code == 403
    assert "coordinator" in error.value.detail
    assert steer.await_count == enqueue.await_count == 0


async def test_worker_message_rejected_before_metadata_or_queue_write(
    monkeypatch: pytest.MonkeyPatch, worker_task: store.TaskRecord
) -> None:
    client = SimpleNamespace(threads=AsyncMock())
    client.threads.get.return_value = {
        "thread_id": "worker",
        "metadata": {"owner_login": "mason", "visibility": "private"},
    }
    queue = AsyncMock()
    monkeypatch.setattr(handlers, "langgraph_client", lambda: client)
    monkeypatch.setattr(handlers, "queue_message_for_thread", queue)
    with pytest.raises(HTTPException) as error:
        await handlers.send_dashboard_message(
            "worker", "mason", runs.ThreadMessageBody(content="Do something else")
        )
    assert error.value.status_code == 403
    assert client.threads.update.await_count == queue.await_count == 0


async def test_shared_sandbox_cannot_spawn_using_coordinator_identity(
    monkeypatch: pytest.MonkeyPatch, worker_task: store.TaskRecord
) -> None:
    load = AsyncMock()
    monkeypatch.setattr(tool_runtime, "load_tool_surface", load)
    with pytest.raises(HTTPException) as error:
        await tool_routes.invoke_tool(
            "spawn_worker",
            ToolArguments(root={"instructions": "Spawn a sibling"}),
            ToolAccess(thread_id="coordinator", sandbox_id="sandbox"),
        )
    assert error.value.status_code == 403
    assert load.await_count == 0


async def test_shared_sandbox_responses_rejects_before_reserving_or_creating_guest(
    monkeypatch: pytest.MonkeyPatch, worker_task: store.TaskRecord
) -> None:
    from agent.openai_responses import conversations

    transaction = AsyncMock()
    monkeypatch.setattr(conversations.postgres, "transaction", transaction)
    caller = SandboxCaller("coordinator", "sandbox", {"owner_login": "mason"})
    with pytest.raises(HTTPException) as error:
        async with caller.reserve_capacity(None):
            pytest.fail("A task sandbox was allowed to create a guest")
    assert error.value.status_code == 403
    assert transaction.call_count == 0


async def test_persisted_authority_outage_is_not_treated_as_unregistered(
    monkeypatch: pytest.MonkeyPatch, worker_task: store.TaskRecord
) -> None:
    monkeypatch.setattr(
        store, "task_for_thread", AsyncMock(side_effect=RuntimeError("database down"))
    )
    with pytest.raises(RuntimeError, match="database down"):
        await ingress.assert_user_facing_thread("worker")


@pytest.mark.parametrize(
    "graph_name", ["chat", "reviewer", "analyzer", "review-scout", "scheduler"]
)
@pytest.mark.parametrize("execution_marker", [True, False])
async def test_task_identity_cannot_switch_to_an_unguarded_graph(
    worker_task: store.TaskRecord, graph_name: str, execution_marker: bool
) -> None:
    from agent.analyzer import get_analyzer
    from agent.chat import get_chat_agent
    from agent.review_scout.graph import get_review_scout
    from agent.reviewer import get_reviewer_agent
    from agent.scheduler import get_scheduler

    factories = {
        "chat": get_chat_agent,
        "reviewer": get_reviewer_agent,
        "analyzer": get_analyzer,
        "review-scout": get_review_scout,
    }
    for thread_id in ("worker", "coordinator"):
        config = {
            "configurable": {"thread_id": thread_id, "__is_for_execution__": execution_marker}
        }
        with pytest.raises(TaskPermissionError, match="ordinary agent graph"):
            if graph_name == "scheduler":
                await get_scheduler(config).ainvoke({"task": "reconcile"})
            else:
                await factories[graph_name](config)


@pytest.mark.parametrize("approved", [True, False])
async def test_worker_workflow_decision_resumes_coordinator(
    monkeypatch: pytest.MonkeyPatch, worker_task: store.TaskRecord, approved: bool
) -> None:
    from agent.threads import workflow_approval, workflow_approval_api

    metadata: dict[str, dict[str, object]] = {
        thread_id: {"owner_login": "mason", "visibility": "private", "source": "dashboard"}
        for thread_id in ("worker", "coordinator")
    }

    async def get(thread_id: str) -> dict[str, object]:
        return {"metadata": metadata[thread_id]}

    async def update(thread_id: str, *, metadata: dict[str, object]) -> None:
        current = await get(thread_id)
        current["metadata"].update(metadata)

    async def fetch(thread_id: str) -> dict[str, object]:
        return metadata[thread_id]

    resumed: list[str] = []

    async def dispatch_run(
        thread_id: str, content: str, config: object, **kwargs: object
    ) -> dict[str, str]:
        await ingress.assert_user_facing_thread(thread_id)
        resumed.append(thread_id)
        return {"run_id": "continuation"}

    client = SimpleNamespace(threads=SimpleNamespace(get=get, update=update))
    monkeypatch.setattr(workflow_approval, "get_client", lambda: client)
    monkeypatch.setattr(workflow_approval_api, "fetch_thread_metadata", fetch)
    monkeypatch.setattr(workflow_approval_api, "dispatch_agent_run", dispatch_run)
    await workflow_approval.ensure_workflow_push_pending(
        "worker",
        fingerprint="fp",
        repo="owner/repo",
        branch="fix",
        base_sha="base",
        head_sha="head",
        files=[".github/workflows/ci.yml"],
    )
    decide = (
        workflow_approval_api.approve_workflow_push
        if approved
        else workflow_approval_api.reject_workflow_push
    )
    with pytest.raises(HTTPException) as denied:
        await decide("worker", "fp", {"sub": "another-user"})
    assert denied.value.status_code == 404
    assert resumed == []
    await decide("worker", "fp", {"sub": "mason"})
    assert await workflow_approval.workflow_push_approved("worker", "fp") is approved
    assert resumed == ["coordinator"]


@pytest.mark.parametrize("completed,pending_result", [(False, False), (True, True), (True, False)])
async def test_coordinator_deletion_waits_for_task_and_worker_results(
    monkeypatch: pytest.MonkeyPatch,
    worker_task: store.TaskRecord,
    completed: bool,
    pending_result: bool,
) -> None:
    from dataclasses import replace

    task = replace(worker_task, status="completed" if completed else "active")
    monkeypatch.setattr(store, "task_for_thread", AsyncMock(return_value=task))
    monkeypatch.setattr(
        store,
        "pending_events",
        AsyncMock(
            return_value=[
                store.TaskEvent("event", task.id, "worker", "completed", "Tests pass", False)
            ]
            if pending_result
            else []
        ),
    )
    client = SimpleNamespace(threads=AsyncMock(), runs=AsyncMock())
    client.threads.get.return_value = {
        "metadata": {
            "owner_login": "mason",
            "visibility": "private",
            "latest_run_id": "run",
        }
    }
    transcript_delete = AsyncMock()
    monkeypatch.setattr(handlers, "langgraph_client", lambda: client)
    monkeypatch.setattr(handlers, "delete_transcript", transcript_delete)
    if not completed or pending_result:
        with pytest.raises(HTTPException) as error:
            await handlers.delete_dashboard_thread("coordinator", "mason")
        assert error.value.status_code == 409
        client.runs.cancel.assert_not_awaited()
        client.threads.delete.assert_not_awaited()
        transcript_delete.assert_not_awaited()
    else:
        await handlers.delete_dashboard_thread("coordinator", "mason")
        client.threads.delete.assert_awaited_once_with("coordinator")
        transcript_delete.assert_awaited_once_with("coordinator")
