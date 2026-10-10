"""Let the model say what each sandbox command does.

Only the schema the model sees gains `description`. The call keeps it in its args for
the timeline and Slack, and Deep Agents' `ExecuteSchema` ignores the extra field when
the command runs.
"""

from collections.abc import Awaitable, Callable

from deepagents.middleware.filesystem import ExecuteSchema
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.tools import BaseTool
from pydantic import Field

from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt


class DescribedExecuteSchema(ExecuteSchema):
    description: str | None = Field(default=None, description=prompt("tools/command-description"))


class DescribeCommandsMiddleware(OpenSWEMiddleware):
    """Offer `execute` with an optional `description` of the command."""

    state_schema = AgentState

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        tools = [
            tool.model_copy(update={"args_schema": DescribedExecuteSchema})
            if isinstance(tool, BaseTool) and tool.name == "execute"
            else tool
            for tool in request.tools
        ]
        return await handler(request.override(tools=tools))
