import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.middleware.task_role import TaskRoleMiddleware
from agent.tasks import policy


@dataclass
class _Task:
    id: str = "task-1"
    coordinator_thread_id: str = "coordinator"
    delegated: bool = False
    status: str = "active"
    acceptance_criteria: list[str] = field(default_factory=lambda: ["The login error is fixed"])
    title: str = "Fix login"


@dataclass(frozen=True)
class _Membership:
    thread_id: str
    role: Literal["coordinator", "worker"]
    task_id: str = "task-1"


class _TaskStore:
    def __init__(self) -> None:
        self.task = _Task()
        self.members = {
            "coordinator": _Membership("coordinator", "coordinator"),
            "worker": _Membership("worker", "worker"),
        }
        self.condition = asyncio.Condition()
        self.owners: dict[str, dict[asyncio.Task[object] | None, bool]] = {}

    @asynccontextmanager
    async def thread_lock(self, thread_id: str, *, shared: bool = False) -> AsyncIterator[None]:
        task = asyncio.current_task()
        owners = self.owners.setdefault(thread_id, {})
        if task in owners:
            assert not owners[task] or shared
            yield
            return
        async with self.condition:
            await self.condition.wait_for(lambda: not owners or (shared and all(owners.values())))
            owners[task] = shared
        try:
            yield
        finally:
            async with self.condition:
                del owners[task]
                self.condition.notify_all()

    async def task_for_thread(self, thread_id: str) -> _Task | None:
        return self.task if thread_id in self.members else None

    async def membership_for_thread(self, thread_id: str) -> _Membership | None:
        return self.members.get(thread_id)


@pytest.fixture
def task_store(monkeypatch: pytest.MonkeyPatch) -> _TaskStore:
    store = _TaskStore()
    monkeypatch.setattr(policy.store, "thread_lock", store.thread_lock)
    monkeypatch.setattr(policy.store, "task_for_thread", store.task_for_thread)
    monkeypatch.setattr(policy.store, "membership_for_thread", store.membership_for_thread)
    return store


def _tool_request(name: str, args: dict[str, object] | None = None) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {}, "id": "call-1", "type": "tool_call"},
        tool=None,
        state={"messages": [], "task_role": "coordinator", "delegated": False},
        runtime=ToolRuntime(
            state={"messages": []},
            context=None,
            config={"configurable": {"thread_id": "coordinator", "task_role": "coordinator"}},
            stream_writer=lambda _: None,
            tool_call_id="call-1",
            store=None,
        ),
    )


async def test_inline_subagents_are_rejected_before_execution() -> None:
    reached_handler = False
    with pytest.raises(policy.TaskPermissionError, match="Synchronous"):
        async with policy.authorize_tool("not-enrolled", "task", {}):
            reached_handler = True
    assert not reached_handler


@pytest.mark.parametrize("name", ["spawn_worker", "start_thread"])
async def test_worker_cannot_use_restricted_tools_despite_forged_runtime_role(
    task_store: _TaskStore, name: str
) -> None:
    executed = False

    async def handler(request: ToolCallRequest) -> ToolMessage | Command:
        nonlocal executed
        executed = True
        return ToolMessage(content="executed", tool_call_id="call-1")

    result = await TaskRoleMiddleware("worker").awrap_tool_call(_tool_request(name), handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert not executed
    async with policy.authorize_tool("worker", "report_worker_progress", {"request_help": True}):
        pass
    async with policy.authorize_tool("worker", "execute", {"command": "pytest tests/login"}):
        pass


@pytest.mark.parametrize(
    "name,args",
    [
        ("execute", {"command": "pytest"}),
        ("background_task", {"action": "stop", "task_id": "job-1"}),
    ],
)
async def test_delegated_coordinator_remains_restricted_without_active_workers(
    task_store: _TaskStore, name: str, args: dict[str, object]
) -> None:
    async with policy.authorize_tool("coordinator", name, args):
        pass
    task_store.task.delegated = True
    task_store.members.pop("worker")
    with pytest.raises(policy.TaskPermissionError, match="permanently"):
        async with policy.authorize_tool("coordinator", name, args):
            pytest.fail("implementation was admitted")
    async with policy.authorize_tool("coordinator", "background_task", {"action": "status"}):
        pass


@pytest.mark.parametrize("thread_id", ["coordinator", "worker", "not-enrolled"])
async def test_shared_sandbox_cannot_impersonate_coordinator(
    task_store: _TaskStore, thread_id: str
) -> None:
    for name in ("spawn_worker", "set_task", "report_worker_progress", "start_thread"):
        with pytest.raises(policy.TaskPermissionError, match="Shared sandbox"):
            async with policy.authorize_tool(thread_id, name, {}, sandbox_capability=True):
                pytest.fail("task mutation was admitted")
    if thread_id != "not-enrolled":
        for name in ("execute", "message_worker", "unknown_integration"):
            with pytest.raises(policy.TaskPermissionError):
                async with policy.authorize_tool(thread_id, name, {}, sandbox_capability=True):
                    pytest.fail("shared capability admitted a mutation")
        async with policy.authorize_tool(thread_id, "get_task", {}, sandbox_capability=True):
            pass


@pytest.mark.parametrize("thread_id", ["coordinator", "worker"])
async def test_client_tool_cannot_borrow_a_read_tool_name(
    task_store: _TaskStore, thread_id: str
) -> None:
    task_store.task.delegated = True
    with pytest.raises(policy.TaskPermissionError, match="Client-defined"):
        async with policy.authorize_tool(thread_id, "get_task", {}, client_tool=True):
            pytest.fail("client execution was admitted")


@pytest.mark.parametrize("delegation_first", [True, False])
async def test_delegation_and_implementation_are_serialized_through_the_effect(
    task_store: _TaskStore, delegation_first: bool
) -> None:
    admitted = asyncio.Event()
    attempted = asyncio.Event()
    release = asyncio.Event()
    effects: list[str] = []

    async def first() -> None:
        name = "spawn_worker" if delegation_first else "execute"
        async with policy.authorize_tool("coordinator", name, {}):
            admitted.set()
            await release.wait()
            if delegation_first:
                task_store.task.delegated = True
            effects.append(name)

    async def second() -> None:
        attempted.set()
        name = "execute" if delegation_first else "spawn_worker"
        try:
            async with policy.authorize_tool("coordinator", name, {}):
                if not delegation_first:
                    task_store.task.delegated = True
                effects.append(name)
        except policy.TaskPermissionError:
            effects.append("denied")

    first_call = asyncio.create_task(first())
    await admitted.wait()
    second_call = asyncio.create_task(second())
    await attempted.wait()
    assert effects == []
    release.set()
    await asyncio.gather(first_call, second_call)
    assert effects == (
        ["spawn_worker", "denied"] if delegation_first else ["execute", "spawn_worker"]
    )


async def test_structured_task_tool_can_reenter_its_execution_lock(task_store: _TaskStore) -> None:
    from dataclasses import replace

    from langchain_core.tools import StructuredTool

    async def spawn_worker() -> str:
        async with policy.authorize_tool("coordinator", "spawn_worker", {}):
            task_store.task.delegated = True
            return "worker"

    tool = StructuredTool.from_function(coroutine=spawn_worker, description="Delegate work")
    request = replace(_tool_request("spawn_worker"), tool=tool)

    async def handler(current: ToolCallRequest) -> ToolMessage | Command:
        assert current.tool is not None
        result = await current.tool.ainvoke(current.tool_call)
        assert isinstance(result, ToolMessage)
        return result

    result = await asyncio.wait_for(
        TaskRoleMiddleware("coordinator").awrap_tool_call(request, handler), timeout=2
    )
    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert task_store.task.delegated
    assert tool.coroutine is spawn_worker


@pytest.mark.parametrize("thread_id", ["worker", "coordinator"])
async def test_dynamic_integration_cannot_bypass_task_permissions(
    task_store: _TaskStore, thread_id: str
) -> None:
    from dataclasses import replace

    from langchain_core.tools import StructuredTool

    from agent.middleware.dynamic_tools import DynamicToolMiddleware

    task_store.task.delegated = True
    effects: list[str] = []

    async def spawn_external_agent() -> str:
        effects.append("spawned")
        return "started"

    tool = StructuredTool.from_function(
        coroutine=spawn_external_agent, description="Start an external agent"
    )
    dynamic = DynamicToolMiddleware({"Agents": [tool]})
    request = replace(
        _tool_request(tool.name),
        state={"messages": [], "loaded_integration_tools": [tool.name]},
    )

    async def execute(current: ToolCallRequest) -> ToolMessage | Command:
        assert current.tool is not None
        result = await current.tool.ainvoke(current.tool_call)
        assert isinstance(result, ToolMessage)
        return result

    async def load(current: ToolCallRequest) -> ToolMessage | Command:
        return await dynamic.awrap_tool_call(current, execute)

    result = await TaskRoleMiddleware(thread_id).awrap_tool_call(request, load)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert effects == []


async def test_alternate_graph_rechecks_permission_before_resumed_tool_effect(
    task_store: _TaskStore,
) -> None:
    from agent.middleware.task_role import NonTaskGraphMiddleware

    task_store.members.pop("coordinator")
    middleware = NonTaskGraphMiddleware("coordinator")
    effects: list[str] = []

    async def handler(request: ToolCallRequest) -> ToolMessage | Command:
        effects.append("executed")
        return ToolMessage(content="executed", tool_call_id="call-1")

    await middleware.awrap_tool_call(_tool_request("execute"), handler)
    task_store.members["coordinator"] = _Membership("coordinator", "coordinator")
    with pytest.raises(policy.TaskPermissionError, match="ordinary agent graph"):
        await middleware.awrap_tool_call(_tool_request("execute"), handler)
    assert effects == ["executed"]


async def test_nested_sandbox_tool_finishes_while_delegation_waits(
    task_store: _TaskStore,
) -> None:
    from agent.tasks.ingress import require_sandbox_tool_access

    attempted = asyncio.Event()
    effects: list[str] = []

    async def delegate() -> None:
        attempted.set()
        async with policy.authorize_tool("coordinator", "set_task", {}):
            effects.append("delegated")

    async def nested_request() -> ToolMessage | Command:
        await require_sandbox_tool_access("coordinator", "read_file", {})

        async def read_file(request: ToolCallRequest) -> ToolMessage:
            effects.append("read")
            return ToolMessage(content="file contents", tool_call_id="call-1")

        return await TaskRoleMiddleware("coordinator", sandbox_capability=True).awrap_tool_call(
            _tool_request("read_file"), read_file
        )

    async def execute(request: ToolCallRequest) -> ToolMessage | Command:
        writer = asyncio.create_task(delegate())
        try:
            await attempted.wait()
            await asyncio.sleep(0.1)
            result = await asyncio.create_task(nested_request())
            assert effects == ["read"]
            return result
        finally:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)

    async with asyncio.timeout(5):
        result = await TaskRoleMiddleware("coordinator").awrap_tool_call(
            _tool_request("execute"), execute
        )
    assert isinstance(result, ToolMessage)
    assert result.content == "file contents"
