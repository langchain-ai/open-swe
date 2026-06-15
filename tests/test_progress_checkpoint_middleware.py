from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.progress_checkpoint import (
    CHECKPOINT_EVERY_N_TOOL_CALLS,
    progress_checkpoint,
)


def _ai_with_tool_call(name: str, command: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"command": command}, "id": call_id}],
    )


def _build_messages_with_tool_calls(n: int, *, command: str = "ls") -> list:
    messages: list = [HumanMessage(content="start")]
    for i in range(n):
        messages.append(_ai_with_tool_call("execute", command, f"tc-{i}"))
        messages.append(ToolMessage(content="ok", tool_call_id=f"tc-{i}"))
    return messages


class TestProgressCheckpoint:
    @pytest.mark.asyncio
    async def test_no_nudge_below_threshold(self) -> None:
        state = {"messages": _build_messages_with_tool_calls(CHECKPOINT_EVERY_N_TOOL_CALLS - 1)}
        result = await progress_checkpoint.abefore_model(state, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_injects_nudge_at_first_bucket(self) -> None:
        state = {"messages": _build_messages_with_tool_calls(CHECKPOINT_EVERY_N_TOOL_CALLS)}
        result = await progress_checkpoint.abefore_model(state, MagicMock())
        assert result is not None
        nudge = result["messages"][0]
        assert "[progress-checkpoint] bucket=1" in nudge.content
        assert str(CHECKPOINT_EVERY_N_TOOL_CALLS) in nudge.content

    @pytest.mark.asyncio
    async def test_idempotent_within_same_bucket(self) -> None:
        msgs = _build_messages_with_tool_calls(CHECKPOINT_EVERY_N_TOOL_CALLS)
        first = await progress_checkpoint.abefore_model({"messages": msgs}, MagicMock())
        assert first is not None
        # Simulate the nudge having been appended to state, then re-run before model.
        msgs_after = msgs + [first["messages"][0]]
        second = await progress_checkpoint.abefore_model({"messages": msgs_after}, MagicMock())
        assert second is None

    @pytest.mark.asyncio
    async def test_fires_again_at_next_bucket(self) -> None:
        # 50 tool calls -> bucket 1 fires
        msgs = _build_messages_with_tool_calls(50)
        first = await progress_checkpoint.abefore_model({"messages": msgs}, MagicMock())
        assert first is not None
        assert "bucket=1" in first["messages"][0].content

        # Now extend to 80 total tool calls and include the prior nudge — bucket 2 should fire.
        msgs_with_nudge = msgs + [first["messages"][0]]
        msgs_at_80 = msgs_with_nudge + _build_messages_with_tool_calls(30)[1:]
        second = await progress_checkpoint.abefore_model({"messages": msgs_at_80}, MagicMock())
        assert second is not None
        assert "bucket=2" in second["messages"][0].content

    @pytest.mark.asyncio
    async def test_does_not_terminate_run(self) -> None:
        state = {"messages": _build_messages_with_tool_calls(CHECKPOINT_EVERY_N_TOOL_CALLS)}
        result = await progress_checkpoint.abefore_model(state, MagicMock())
        # The middleware only returns {"messages": [...]} — never goto/jump_to/end signals.
        assert set(result.keys()) == {"messages"}

    def test_duplicate_command_surface_is_visible(self) -> None:
        """Regression guard: duplicate `args.command` values must be observable from messages."""
        msgs = _build_messages_with_tool_calls(5, command="grep foo .")
        commands = [
            tc["args"]["command"]
            for m in msgs
            for tc in (getattr(m, "tool_calls", None) or [])
            if "command" in tc.get("args", {})
        ]
        assert len(commands) == 5
        # All identical => duplicates beyond a small threshold are visible.
        assert commands.count("grep foo .") > 2
