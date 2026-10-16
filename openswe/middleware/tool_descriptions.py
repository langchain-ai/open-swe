"""Have the model label its calls of chosen tools with a human-readable description.

Each chosen tool the model is offered gains a required ``display_description`` argument.
The tool call on the AI message keeps it, so the transcript and the dashboard can
label the step with it, and it is removed before the tool itself runs.
"""

from collections.abc import Awaitable, Callable
from typing import Final

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import JsonValue

from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt
from openswe.sandboxes.tool_runtime import tool_parameters

DISPLAY_DESCRIPTION: Final = "display_description"


class ToolDescriptionMiddleware(OpenSWEMiddleware):
    """Add a ``display_description`` argument to the named tools, and drop it before the call.

    Place it inside code mode, which binds the request's tools to ``eval`` before
    this rewrites them: tools called from code take no description.
    """

    state_schema = AgentState

    def __init__(self, tools: frozenset[str]) -> None:
        self._tools = tools
        # Keyed by identity: a tool memoizes the schema it sends, so a copy is reused.
        self._offered: dict[int, tuple[BaseTool, BaseTool]] = {}

    def _offered_as(self, tool: BaseTool) -> BaseTool:
        """``tool`` as the model sees it: itself when it already takes the argument."""
        cached = self._offered.get(id(tool))
        if cached is not None and cached[0] is tool:
            return cached[1]
        offered = self._describe(tool)
        self._offered[id(tool)] = (tool, offered)
        return offered

    @staticmethod
    def _describe(tool: BaseTool) -> BaseTool:
        parameters = tool_parameters(tool)
        properties = parameters.get("properties")
        if isinstance(properties, dict) and DISPLAY_DESCRIPTION in properties:
            return tool
        required = parameters.get("required")
        schema: dict[str, JsonValue] = {
            **parameters,
            "type": "object",
            # First, so the model says what it is doing before how.
            "properties": {
                DISPLAY_DESCRIPTION: {
                    "type": "string",
                    "description": prompt("tools/display-description"),
                },
                **(properties if isinstance(properties, dict) else {}),
            },
            "required": [DISPLAY_DESCRIPTION, *(required if isinstance(required, list) else [])],
        }
        return tool.model_copy(update={"args_schema": schema})

    def _offer(self, request: ModelRequest) -> ModelRequest:
        tools = [
            self._offered_as(tool)
            if isinstance(tool, BaseTool) and tool.name in self._tools
            else tool
            for tool in request.tools
        ]
        return request.override(tools=tools)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._offer(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        call = request.tool_call
        if (
            call["name"] not in self._tools
            or DISPLAY_DESCRIPTION not in call["args"]
            or request.tool is not None
            and self._offered_as(request.tool) is request.tool
        ):
            return await handler(request)
        args = {key: value for key, value in call["args"].items() if key != DISPLAY_DESCRIPTION}
        return await handler(request.override(tool_call={**call, "args": args}))
