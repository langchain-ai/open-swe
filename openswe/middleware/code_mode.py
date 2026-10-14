"""Keep turn-scoped MCP code mode usable when a checkpoint skips before-agent hooks."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import cast

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langchain_quickjs import CodeInterpreterMiddleware
from langchain_quickjs.middleware import REPLState, filter_tools_for_ptc
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command


class ResumableCodeInterpreterMiddleware(CodeInterpreterMiddleware[None, object]):
    """Initialize missing interpreter state on paths that resume after before-agent."""

    async def abefore_model(
        self, state: REPLState, runtime: Runtime[None]
    ) -> dict[str, object] | None:
        """Checkpoint a lazy slot so subsequent tools and cleanup share it."""
        slot_id = state.get("_quickjs_slot_id")
        if isinstance(slot_id, str) and slot_id:
            return None
        return await super().abefore_agent(state, runtime)

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelResponse[object]:
        updates = await self.abefore_model(cast(REPLState, request.state), request.runtime)
        if updates:
            request = request.override(state=cast(REPLState, {**request.state, **updates}))
        return await super().awrap_model_call(request, handler)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[object]]],
    ) -> ToolMessage | Command[object]:
        if request.tool is None and self._ptc is not None:
            for tool in filter_tools_for_ptc([], self._ptc, self_tool_name=self._tool_name):
                if tool.name == request.tool_call["name"]:
                    request = request.override(tool=tool)
                    break
        state = cast(REPLState, request.state)
        updates = await self.abefore_model(state, Runtime(context=request.runtime.context))
        if updates:
            state = cast(REPLState, {**state, **updates})
            request = replace(request, state=state, runtime=replace(request.runtime, state=state))
        return await handler(request)

    async def aafter_agent(
        self, state: REPLState, runtime: Runtime[None]
    ) -> dict[str, object] | None:
        slot_id = state.get("_quickjs_slot_id")
        if not isinstance(slot_id, str) or not slot_id:
            return None
        return await super().aafter_agent(state, runtime)
