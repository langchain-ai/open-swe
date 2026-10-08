"""Stop the run at calls to tools the Responses client runs itself.

A call gets a placeholder result, and the run ends before the next model call.
The client's real result arrives with the next run under the placeholder's
message id, replacing it in place.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import AgentState, hook_config
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from openswe.middleware.trace import OpenSWEMiddleware
from openswe.openai_responses.client_tools import PENDING_ARTIFACT_KEY, ClientToolSpec
from openswe.prompts import prompt


async def _runs_on_the_client(**_: object) -> str:
    raise RuntimeError("client tools are answered by ClientToolsMiddleware")


class ClientToolsMiddleware(OpenSWEMiddleware):
    def __init__(self, specs: Sequence[ClientToolSpec]) -> None:
        self.tools: list[BaseTool] = [
            StructuredTool(
                name=spec.name,
                description=spec.description,
                args_schema=spec.parameters,
                coroutine=_runs_on_the_client,
            )
            for spec in specs
        ]
        self._names = frozenset(spec.name for spec in specs)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        name = request.tool_call["name"]
        call_id = request.tool_call["id"]
        if name not in self._names or not call_id:
            return await handler(request)
        return ToolMessage(
            content=prompt("client-tools/pending-result"),
            tool_call_id=call_id,
            name=name,
            id=ClientToolSpec.result_message_id(call_id),
            artifact={PENDING_ARTIFACT_KEY: True},
        )

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                return None
            if isinstance(message.artifact, dict) and message.artifact.get(PENDING_ARTIFACT_KEY):
                return {"jump_to": "end"}
        return None
