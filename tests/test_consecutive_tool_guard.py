from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.consecutive_tool_guard import (
    ConsecutiveToolGuardMiddleware,
    consecutive_write_todos_count,
)


def _write_todos_ai(n: int = 1) -> list[AIMessage]:
    return [
        AIMessage(content="", tool_calls=[{"name": "write_todos", "args": {}, "id": f"t{i}"}])
        for i in range(n)
    ]


class TestConsecutiveWriteTodosCount:
    def test_counts_trailing_write_todos(self) -> None:
        assert consecutive_write_todos_count(_write_todos_ai(4)) == 4

    def test_resets_on_state_changing_tool(self) -> None:
        messages = [
            *_write_todos_ai(3),
            AIMessage(content="", tool_calls=[{"name": "edit_file", "args": {}, "id": "e1"}]),
            *_write_todos_ai(2),
        ]
        assert consecutive_write_todos_count(messages) == 2

    def test_zero_when_no_write_todos(self) -> None:
        messages = [
            AIMessage(content="", tool_calls=[{"name": "execute", "args": {}, "id": "x1"}]),
        ]
        assert consecutive_write_todos_count(messages) == 0


class TestConsecutiveToolGuardMiddleware:
    def _runtime(self) -> MagicMock:
        return MagicMock()

    def test_no_action_below_soft_threshold(self) -> None:
        guard = ConsecutiveToolGuardMiddleware(soft_threshold=5, hard_ceiling=10)
        result = guard.before_model({"messages": _write_todos_ai(4)}, self._runtime())
        assert result is None

    def test_nudge_at_soft_threshold(self) -> None:
        guard = ConsecutiveToolGuardMiddleware(soft_threshold=5, hard_ceiling=10)
        result = guard.before_model({"messages": _write_todos_ai(5)}, self._runtime())
        assert result is not None
        assert "jump_to" not in result
        assert "planning_loop_warning" in result["messages"][0].content

    def test_nudge_not_repeated(self) -> None:
        guard = ConsecutiveToolGuardMiddleware(soft_threshold=5, hard_ceiling=10)
        messages = _write_todos_ai(5)
        first = guard.before_model({"messages": messages}, self._runtime())
        messages.append(first["messages"][0])
        assert guard.before_model({"messages": messages}, self._runtime()) is None

    def test_hard_ceiling_ends_run(self) -> None:
        guard = ConsecutiveToolGuardMiddleware(soft_threshold=5, hard_ceiling=10)
        result = guard.before_model({"messages": _write_todos_ai(10)}, self._runtime())
        assert result is not None
        assert result["jump_to"] == "end"
        assert "write_todos loop guard triggered" in result["messages"][0].content

    @pytest.mark.asyncio
    async def test_closeout_posts_slack_reply(self) -> None:
        guard = ConsecutiveToolGuardMiddleware(soft_threshold=5, hard_ceiling=10)
        state = {
            "messages": [
                AIMessage(content="write_todos loop guard triggered: 10 consecutive write_todos")
            ]
        }
        with (
            patch(
                "agent.middleware.consecutive_tool_guard.get_config",
                return_value={
                    "configurable": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.2"}}
                },
            ),
            patch(
                "agent.middleware.consecutive_tool_guard.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await guard.aafter_agent(state, self._runtime())
        assert result is None
        mock_post.assert_awaited_once()
        assert mock_post.await_args.args[0:2] == ("C1", "1.2")

    @pytest.mark.asyncio
    async def test_closeout_skipped_without_marker(self) -> None:
        guard = ConsecutiveToolGuardMiddleware(soft_threshold=5, hard_ceiling=10)
        state = {"messages": [HumanMessage(content="keep going")]}
        with patch(
            "agent.middleware.consecutive_tool_guard.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            result = await guard.aafter_agent(state, self._runtime())
        assert result is None
        mock_post.assert_not_called()
