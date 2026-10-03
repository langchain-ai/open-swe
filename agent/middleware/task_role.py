import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import wraps

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import prompt
from agent.tasks import store
from agent.tasks.ingress import assert_non_task_graph
from agent.tasks.policy import (
    TASK_MUTATION_TOOLS,
    TaskPermissionError,
    authorize_tool,
    policy_for_thread,
    tool_denial,
)

logger = logging.getLogger(__name__)
ROLE_MESSAGE_ID = "open-swe-task-role"


class NonTaskGraphMiddleware(OpenSWEMiddleware[AgentState]):
    def __init__(self, thread_id: str) -> None:
        self._thread_id = thread_id

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> None:
        await assert_non_task_graph(self._thread_id)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        await assert_non_task_graph(self._thread_id)
        return await handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call["name"] == "task":
            await assert_non_task_graph(self._thread_id)
            return await handler(request)
        async with store.thread_lock(self._thread_id):
            await assert_non_task_graph(self._thread_id)
            return await handler(request)


class TaskRoleMiddleware(OpenSWEMiddleware[AgentState]):
    def __init__(
        self,
        thread_id: str,
        *,
        client_tool_names: frozenset[str] = frozenset(),
        sandbox_capability: bool = False,
    ) -> None:
        self._thread_id = thread_id
        self._client_tool_names = client_tool_names
        self._sandbox_capability = sandbox_capability

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        policy = await policy_for_thread(self._thread_id)
        permitted: list[str] = []
        denied: list[str] = []
        for tool in request.tools:
            name = tool.name if isinstance(tool, BaseTool) else tool.get("name")
            if not isinstance(name, str):
                continue
            reason = tool_denial(
                policy,
                name,
                {"action": "status"} if name == "background_task" else {},
                client_tool=name in self._client_tool_names,
                sandbox_capability=self._sandbox_capability,
            )
            if reason is None:
                permitted.append(
                    "background_task (status or list only)"
                    if name == "background_task" and (policy.delegated or self._sandbox_capability)
                    else name
                )
            else:
                denied.append(name)
        role_message = HumanMessage(
            content=prompt(
                "tasks/role",
                role=policy.role,
                delegated=policy.delegated,
                task_id=policy.task_id,
                title=policy.title,
                coordinator_thread_id=policy.coordinator_thread_id,
                acceptance_criteria=policy.acceptance_criteria,
                status=policy.status,
                permitted_tools=sorted(set(permitted)),
                denied_tools=sorted(set(denied)),
                sandbox_capability=self._sandbox_capability,
            ),
            id=ROLE_MESSAGE_ID,
        )
        messages = [role_message, *(m for m in request.messages if m.id != ROLE_MESSAGE_ID)]
        return await handler(request.override(messages=messages))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        name = request.tool_call["name"]
        client_tool = name in self._client_tool_names
        tool = request.tool
        try:
            if (
                name in TASK_MUTATION_TOOLS | {"get_task"}
                and not client_tool
                and isinstance(tool, StructuredTool)
                and tool.coroutine is not None
            ):
                async with authorize_tool(
                    self._thread_id,
                    name,
                    request.tool_call["args"],
                    sandbox_capability=self._sandbox_capability,
                ):
                    coroutine = tool.coroutine

                @wraps(coroutine)
                async def guarded(*args: object, **kwargs: object) -> object:
                    async with authorize_tool(
                        self._thread_id,
                        name,
                        request.tool_call["args"],
                        sandbox_capability=self._sandbox_capability,
                    ):
                        return await coroutine(*args, **kwargs)

                return await handler(
                    replace(request, tool=tool.model_copy(update={"coroutine": guarded}))
                )
            async with authorize_tool(
                self._thread_id,
                name,
                request.tool_call["args"],
                client_tool=client_tool,
                sandbox_capability=self._sandbox_capability,
            ):
                return await handler(request)
        except TaskPermissionError as exc:
            logger.warning(
                "Task tool execution denied",
                extra={"agent_thread_id": self._thread_id, "tool_name": name, "reason": str(exc)},
            )
            return ToolMessage(
                content=str(exc),
                name=name,
                tool_call_id=request.tool_call["id"] or name,
                status="error",
            )
