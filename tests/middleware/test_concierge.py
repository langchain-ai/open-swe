from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.concierge import post_concierge_reply

_CONCIERGE_CONFIG = {
    "configurable": {
        "slack_thread": {
            "channel_id": "D123",
            "thread_ts": "0",
            "reply_thread_ts": "171.123",
            "channel_context": {"is_im": True},
        }
    }
}
_CHANNEL_CONFIG = {
    "configurable": {
        "slack_thread": {
            "channel_id": "C123",
            "thread_ts": "171.123",
            "channel_context": {"is_im": False},
        }
    }
}


class TestPostConciergeReply:
    def _make_runtime(self) -> MagicMock:
        return MagicMock()

    async def _run(self, state: AgentState, config: dict[str, object]) -> AsyncMock:
        with (
            patch("agent.run_config.get_config", return_value=config),
            patch(
                "agent.slack.tools.thread_reply.slack_thread_reply",
                new_callable=AsyncMock,
                return_value={"success": True},
            ) as mock_reply,
        ):
            assert await post_concierge_reply.aafter_agent(state, self._make_runtime()) is None
        return mock_reply

    @pytest.mark.asyncio
    async def test_delivers_the_final_response_to_the_conversation(self) -> None:
        state: AgentState = {
            "messages": [HumanMessage(content="what broke?"), AIMessage(content="A stale cache.")]
        }

        mock_reply = await self._run(state, _CONCIERGE_CONFIG)

        mock_reply.assert_awaited_once()
        assert mock_reply.await_args is not None
        assert mock_reply.await_args.args[0] == "A stale cache."

    @pytest.mark.asyncio
    async def test_leaves_a_channel_thread_to_its_reply_tool(self) -> None:
        state: AgentState = {"messages": [AIMessage(content="A stale cache.")]}

        mock_reply = await self._run(state, _CHANNEL_CONFIG)

        mock_reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_the_step_limit_marker(self) -> None:
        state: AgentState = {
            "messages": [AIMessage(content="Model call limits exceeded: run limit reached")]
        }

        mock_reply = await self._run(state, _CONCIERGE_CONFIG)

        mock_reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_a_run_that_ended_on_a_tool_result(self) -> None:
        state: AgentState = {
            "messages": [AIMessage(content=""), ToolMessage(content="done", tool_call_id="1")]
        }

        mock_reply = await self._run(state, _CONCIERGE_CONFIG)

        mock_reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_delivery_does_not_fail_the_run(self) -> None:
        state: AgentState = {"messages": [AIMessage(content="A stale cache.")]}

        with (
            patch("agent.run_config.get_config", return_value=_CONCIERGE_CONFIG),
            patch(
                "agent.slack.tools.thread_reply.slack_thread_reply",
                new_callable=AsyncMock,
                side_effect=RuntimeError("slack is down"),
            ),
        ):
            assert await post_concierge_reply.aafter_agent(state, self._make_runtime()) is None
