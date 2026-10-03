"""Task role enforcement rejects side effects without hiding tools."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from agent.database import postgres
from agent.middleware import task_authority
from agent.tasks import TaskRole, authority, ensure_task, role, update_task


@pytest.mark.asyncio
async def test_worker_cannot_delegate_and_coordinator_cannot_execute_after_delegation(monkeypatch):
    persisted = TaskRole(uuid4(), "coordinator", "worker", True, ["deliver change"], False)
    lookup = AsyncMock(return_value=persisted)
    monkeypatch.setattr(task_authority, "role", lookup)
    assert await task_authority.reject_tool("worker", "spawn_worker")
    assert await task_authority.reject_tool("worker", "task")
    assert await task_authority.reject_tool("worker", "start_thread")
    assert await task_authority.reject_tool("worker", "execute") is None
    lookup.return_value = TaskRole(persisted.task_id, "coordinator", "coordinator", True, [], False)
    for name in (
        "execute",
        "background_execute",
        "write_file",
        "edit_file",
        "http_request",
        "mcp_custom",
    ):
        assert await task_authority.reject_tool("coordinator", name)
    assert await task_authority.reject_tool("coordinator", "spawn_worker") is None
    assert await task_authority.reject_tool("coordinator", "message_task_thread") is None
    lookup.return_value = TaskRole(
        persisted.task_id, "coordinator", "coordinator", False, [], False
    )
    assert await task_authority.reject_tool("coordinator", "execute") is None


@pytest.mark.asyncio
async def test_unconfigured_database_keeps_normal_tools_but_rejects_delegation(monkeypatch):
    monkeypatch.setattr(postgres, "configured", lambda: False)
    assert await task_authority.reject_tool("thread", "execute") is None
    assert await task_authority.reject_tool("thread", "task")
    async with authority("thread", tool_call=True):
        with pytest.raises(ValueError, match="PostgreSQL"):
            await ensure_task("thread")


@pytest.mark.asyncio
async def test_delegation_serializes_with_implementation_and_never_resets(registry_db):
    await update_task("coordinator", ["ship"], False, "")
    started = asyncio.Event()
    release = asyncio.Event()
    observed = []

    async def implementation():
        async with authority("coordinator"):
            started.set()
            await release.wait()
            observed.append("implementation")

    async def delegate():
        await started.wait()
        async with authority("coordinator"):
            task = await ensure_task("coordinator")
            async with postgres.transaction() as conn:
                await conn.execute(
                    text("UPDATE coordinated_task SET delegated = true WHERE id = :id"),
                    {"id": task.task_id},
                )
            observed.append("delegated")

    first = asyncio.create_task(implementation())
    await started.wait()
    with pytest.raises(PermissionError, match="conflicts with active tools"):
        await delegate()
    assert observed == []
    release.set()
    await first
    await delegate()
    assert observed == ["implementation", "delegated"]
    await update_task("coordinator", ["ship", "verify"], True, "Both criteria satisfied")
    await update_task("coordinator", ["follow-up"], False, "")
    persisted = await role("coordinator")
    assert persisted is not None and persisted.delegated
    assert await task_authority.reject_tool("coordinator", "execute")


@pytest.mark.asyncio
async def test_task_notifications_are_deduplicated_and_survive_retention(registry_db):
    from agent.webhooks.event_matches import EventMatch

    task_id = uuid4()
    for _ in range(2):
        async with postgres.session() as session:
            await EventMatch(
                thread_id="coordinator",
                subscription_id=task_id,
                source="task",
                delivery_id="finished:worker:run",
                content="done",
                run_config={},
            ).record(session)
    async with postgres.transaction() as conn:
        await conn.execute(text("UPDATE event_match SET matched_at = now() - interval '3 days'"))
    owed = await EventMatch.owed("coordinator", [])
    assert len(owed) == 1
    assert await EventMatch.owed("coordinator", EventMatch.messages(owed)) == []


@pytest.mark.asyncio
async def test_worker_reservation_survives_dispatch_failure_and_retries(monkeypatch, registry_db):
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from agent import dispatch
    from agent.tasks import assert_user_entry
    from agent.tools import task_threads
    from agent.webhooks import event_matches

    coordinator = str(uuid4())
    parent = {
        "owner_type": "user",
        "owner_login": "test-user",
        "visibility": "private",
        "workspace": "default",
        "sandbox_id": "sandbox-1",
        "github_token_repositories": ["langchain-ai/open-swe"],
        "participant_logins": ["test-user"],
        "repo_owner": "langchain-ai",
        "repo_name": "open-swe",
    }
    threads = {coordinator: {"metadata": parent, "status": "idle"}}
    states = {}
    client = MagicMock()

    async def create(*, thread_id, metadata, **kwargs):
        threads.setdefault(thread_id, {"metadata": metadata, "status": "idle"})
        return threads[thread_id]

    async def get(thread_id):
        return threads[thread_id]

    async def get_state(thread_id):
        return {"values": {"messages": states.get(thread_id, [])}}

    async def run(thread_id, assistant_id, **kwargs):
        states[thread_id] = kwargs["input"]["messages"]
        return {"run_id": "run-1"}

    client.threads.create = AsyncMock(side_effect=create)
    client.threads.get = AsyncMock(side_effect=get)
    client.threads.get_state = AsyncMock(side_effect=get_state)
    client.runs.create = AsyncMock(side_effect=RuntimeError("dispatch unavailable"))
    monkeypatch.setattr(task_threads, "current_thread", lambda: coordinator)
    monkeypatch.setattr(task_threads, "langgraph_client", lambda: client)
    monkeypatch.setattr(event_matches, "dispatch_client", lambda: client)
    monkeypatch.setattr(task_threads, "COMPLETION_WEBHOOK_URL", "https://example.test/completion")
    monkeypatch.setattr(dispatch, "COMPLETION_WEBHOOK_URL", "https://example.test/completion")
    monkeypatch.setattr(dispatch, "_run_user_id", AsyncMock(return_value=None))
    monkeypatch.setattr("agent.slack.thinking.sync_slack_background_status", AsyncMock())
    monkeypatch.setattr(
        task_threads.thread_runs, "resolve_task_model", AsyncMock(return_value=("model", "high"))
    )
    monkeypatch.setattr(task_threads.thread_runs, "get_profile", AsyncMock(return_value={}))
    monkeypatch.setattr(
        task_threads.thread_runs, "resolve_run_email", AsyncMock(return_value="test@example.com")
    )
    await update_task(coordinator, ["ship and verify"], False, "")
    result = await task_threads.spawn_worker("Implement the change", "model", "high")
    assert result["success"] is False
    worker = result["worker_id"]
    assert isinstance(worker, str)
    coordinator_role = await role(coordinator)
    worker_role = await role(worker)
    assert coordinator_role is not None and coordinator_role.delegated
    assert worker_role is not None and worker_role.role == "worker"
    status = await task_threads.task_status()
    workers = status["workers"]
    assert isinstance(workers, list)
    assert workers[0]["worker_id"] == worker
    assert workers[0]["dispatched"] is False
    metadata = threads[worker]["metadata"]
    assert isinstance(metadata, dict)
    for key, value in parent.items():
        assert metadata[key] == value
    assert metadata["sandbox_host_thread_id"] == coordinator
    assert metadata["resolved_model"] == "model"
    with pytest.raises(HTTPException, match="coordinator"):
        await assert_user_entry(worker)
    client.runs.create.side_effect = run
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import StructuredTool
    from langgraph.prebuilt.tool_node import ToolCallRequest

    monkeypatch.setattr(task_authority, "current_thread", lambda: coordinator)
    tool = StructuredTool.from_function(coroutine=task_threads.control_worker)
    request = ToolCallRequest(
        tool_call={
            "name": "control_worker",
            "args": {"worker_id": worker, "action": "retry"},
            "id": "retry",
        },
        tool=tool,
        state={},
        runtime=MagicMock(),
    )

    async def invoke(request):
        result = await tool.ainvoke(request.tool_call["args"])
        return ToolMessage(content=str(result), tool_call_id="retry")

    replacement = AsyncMock()
    refused = await task_authority.TaskAuthorityMiddleware(
        client_tool_names=frozenset({"control_worker"})
    ).awrap_tool_call(request, replacement)
    assert refused.status == "error"
    replacement.assert_not_called()
    await asyncio.wait_for(
        task_authority.TaskAuthorityMiddleware().awrap_tool_call(request, invoke), 5
    )
    workers = (await task_threads.task_status())["workers"]
    assert isinstance(workers, list)
    assert workers[0]["dispatched"] is True
    dispatched = client.runs.create.call_args.kwargs
    configurable = dispatched["config"]["configurable"]
    assert configurable["github_login"] == "test-user"
    assert configurable["thread_id"] == worker
    assert configurable["workspace"] == "default"
    assert configurable["repo"] == {"owner": "langchain-ai", "name": "open-swe"}
    assert dispatched["durability"] == "sync"
    assert dispatched["multitask_strategy"] == "enqueue"
    assert dispatched["webhook"] == "https://example.test/completion"
    calls = client.runs.create.call_count
    await task_threads.control_worker(worker, "retry")
    assert client.runs.create.call_count == calls
    assert len(await event_matches.EventMatch.owed(worker, [])) == 1
    async with postgres.session() as session:
        await event_matches.EventMatch(
            thread_id=worker,
            subscription_id=uuid4(),
            source="github",
            delivery_id="ci-finished",
            content="CI completed",
            run_config=dispatched["config"]["configurable"],
        ).record(session)
    await event_matches.EventMatch.deliver(worker, "enqueue")
    assert client.runs.create.call_count == calls + 1
    assert "CI completed" in str(client.runs.create.call_args.kwargs["input"])


@pytest.mark.asyncio
async def test_authority_rejects_inherited_nested_execution_but_expires(registry_db):
    release = asyncio.Event()

    async def child():
        with pytest.raises(PermissionError, match="Nested tool"):
            async with authority("coordinator", tool_call=True):
                pytest.fail("nested invocation was allowed")
        await release.wait()
        async with authority("coordinator", tool_call=True):
            return True

    async with authority("coordinator", tool_call=True):
        pending = asyncio.create_task(child())
        await asyncio.sleep(0)
    release.set()
    assert await pending


@pytest.mark.asyncio
async def test_parallel_and_proxy_tool_admissions_release_pool_connections(registry_db):
    from contextvars import Context

    async def proxy():
        async with authority("host", tool_call=True, exclusive=False):
            assert await role("host") is None

    async with authority("host", tool_call=True, exclusive=False):
        await asyncio.wait_for(asyncio.create_task(proxy(), context=Context()), 2)
        with pytest.raises(PermissionError, match="conflicts"):
            await asyncio.create_task(update_task("host", ["ship"], False, ""), context=Context())
        assert postgres.engine().pool.checkedout() == 0
    entered = 0
    ready = asyncio.Event()

    async def call(index):
        nonlocal entered
        async with authority(f"host-{index}", tool_call=True, exclusive=False):
            entered += 1
            if entered == 12:
                ready.set()
            await ready.wait()
            assert await role(f"host-{index}") is None

    await asyncio.wait_for(asyncio.gather(*(call(i) for i in range(12))), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("exclusive", [False, True])
async def test_abandoned_admissions_reclaimed_when_owner_session_ends(registry_db, exclusive):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    owner = uuid4()
    engine = create_async_engine(postgres.uri(), poolclass=NullPool)
    session = await engine.connect()
    await session.execute(
        text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
        {"key": f"task-owner:{owner}"},
    )
    await session.commit()
    admission = uuid4()
    async with postgres.transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO task_tool_admission (id, thread_id, exclusive, owner_id) "
                "VALUES (:id, 'thread', :exclusive, :owner)"
            ),
            {"id": admission, "exclusive": exclusive, "owner": owner},
        )
    with pytest.raises(PermissionError, match="conflicts"):
        async with authority("thread"):
            pytest.fail("Live execution was reclaimed")
    await session.close()
    await engine.dispose()
    async with authority("thread"):
        async with postgres.transaction() as conn:
            assert not await conn.scalar(
                text("SELECT EXISTS (SELECT 1 FROM task_tool_admission WHERE id = :id)"),
                {"id": admission},
            )


@pytest.mark.asyncio
async def test_lost_owner_connection_only_cancels_fenced_tools(registry_db, monkeypatch):
    from agent import tasks

    monkeypatch.setattr(tasks, "_OWNER_GATE", asyncio.Lock())
    await update_task("member", ["ship"], False, "")
    entered = {name: asyncio.Event() for name in ("ordinary", "member", "transition")}
    release = asyncio.Event()

    async def tool(name):
        async with authority(name, exclusive=name == "transition"):
            entered[name].set()
            await release.wait()

    running = {name: asyncio.create_task(tool(name)) for name in entered}
    await asyncio.gather(*(event.wait() for event in entered.values()))
    owner = tasks._OWNER
    assert owner is not None
    await owner.connection.close()
    for name in ("member", "transition"):
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running[name], 3)
    assert not running["ordinary"].done()
    with pytest.raises(PermissionError, match="owner lost"):
        async with authority("new"):
            pytest.fail("Lost ownership allowed admission")
    release.set()
    await running["ordinary"]
    async with authority("thread"):
        assert tasks._OWNER is not owner
