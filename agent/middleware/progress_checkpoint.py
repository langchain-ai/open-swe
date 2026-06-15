"""Before-model middleware that injects periodic summarize-and-replan checkpoints."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentState, before_model
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

CHECKPOINT_EVERY_N_TOOL_CALLS = 40
_CHECKPOINT_MARKER = "[progress-checkpoint]"


def _count_tool_calls(messages: list[Any]) -> int:
    count = 0
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None) or []
        count += len(tool_calls)
    return count


def _already_checkpointed_at(messages: list[Any], bucket: int) -> bool:
    needle = f"{_CHECKPOINT_MARKER} bucket={bucket}"
    for msg in messages:
        content = getattr(msg, "content", "") or ""
        if isinstance(content, str):
            if needle in content:
                return True
        elif isinstance(content, list):
            for block in content:
                text = block.get("text", "") if isinstance(block, dict) else ""
                if isinstance(text, str) and needle in text:
                    return True
    return False


@before_model
async def progress_checkpoint(
    state: AgentState,
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    """Nudge the agent to summarize and replan every N tool calls without ending the run."""
    messages = list(state.get("messages", []) or [])
    tool_calls = _count_tool_calls(messages)
    if tool_calls < CHECKPOINT_EVERY_N_TOOL_CALLS:
        return None

    bucket = tool_calls // CHECKPOINT_EVERY_N_TOOL_CALLS
    if _already_checkpointed_at(messages, bucket):
        return None

    nudge = SystemMessage(
        content=(
            f"{_CHECKPOINT_MARKER} bucket={bucket}\n"
            f"You have made {tool_calls} tool calls in this session. "
            "Before continuing, briefly summarize what you've learned, list "
            "the unresolved unknowns, and propose your next ≤10 steps. "
            "If you are about to issue a command similar to one you ran "
            "earlier, first state what new information it will give you "
            "that the previous run did not."
        )
    )
    return {"messages": [nudge]}
