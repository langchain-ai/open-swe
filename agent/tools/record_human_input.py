"""Tool: ``record_human_input``. Records the review scout's summary of what people asked for."""

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command

MAX_SUMMARY_CHARS = 2_000


async def record_human_input(
    summary: str, tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command:
    """Implement the `record_human_input` tool."""
    trimmed = summary.strip()[:MAX_SUMMARY_CHARS]
    return Command(
        update={
            "human_input_summary": trimmed,
            "messages": [
                ToolMessage(
                    "Recorded." if trimmed else "summary is empty; nothing recorded",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
