"""Tool: ``exit_routing_mode``. Select the model for normal execution."""

from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command

Route = Literal["fast", "balanced", "performance"]


async def exit_routing_mode(
    model_route: Route,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Implement the ``exit_routing_mode`` tool."""
    return Command(
        update={
            "routing_mode": False,
            "model_route": model_route,
            "messages": [
                ToolMessage(
                    content=(
                        f"Routing mode is now inactive. Continue the task with the {model_route} "
                        "model using the repository context and conversation history already gathered."
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
