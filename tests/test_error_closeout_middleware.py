import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphInterrupt

from agent.middleware.error_closeout import wrap_agent_with_error_closeout
from agent.utils.dashboard_handoff import DASHBOARD_HANDOFF_INSTRUCTION

SLACK_CONFIG = {"configurable": {"slack_thread": {"channel_id": "C1", "thread_ts": "171.1"}}}


class _FakeGraph:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self._error = error
        self.config = None
        self.aupdate_state = AsyncMock()

    async def astream(self, graph_input, config=None, **kwargs):
        yield {"messages": []}
        if self._error is not None:
            raise self._error

    async def ainvoke(self, graph_input, config=None, **kwargs):
        if self._error is not None:
            raise self._error
        return {"messages": []}


def _make_graph(*, error: BaseException | None = None) -> _FakeGraph:
    return _FakeGraph(error=error)


async def _drain(graph, graph_input, config):
    async for _ in graph.astream(graph_input, config):
        pass


class TestErrorCloseout:
    @pytest.mark.asyncio
    async def test_posts_slack_reply_on_error(self) -> None:
        graph = _make_graph(error=RuntimeError("git merge --abort failed"))
        wrapped = wrap_agent_with_error_closeout(graph)

        with patch(
            "agent.middleware.error_closeout.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            with pytest.raises(RuntimeError):
                await _drain(wrapped, {"messages": []}, SLACK_CONFIG)

        mock_post.assert_awaited_once()
        assert mock_post.await_args.args[0:2] == ("C1", "171.1")
        assert "git merge --abort failed" in mock_post.await_args.args[2]
        graph.aupdate_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_inline_message_for_web_handoff(self) -> None:
        graph = _make_graph(error=RuntimeError("boom"))
        wrapped = wrap_agent_with_error_closeout(graph)
        graph_input = {"messages": [HumanMessage(content=DASHBOARD_HANDOFF_INSTRUCTION)]}

        with patch(
            "agent.middleware.error_closeout.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            with pytest.raises(RuntimeError):
                await _drain(wrapped, graph_input, SLACK_CONFIG)

        mock_post.assert_not_called()
        graph.aupdate_state.assert_awaited_once()
        posted = graph.aupdate_state.await_args.args[1]["messages"][0]
        assert isinstance(posted, AIMessage)

    @pytest.mark.asyncio
    async def test_inline_message_when_no_slack_thread(self) -> None:
        graph = _make_graph(error=RuntimeError("boom"))
        wrapped = wrap_agent_with_error_closeout(graph)

        with patch(
            "agent.middleware.error_closeout.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            with pytest.raises(RuntimeError):
                await _drain(wrapped, {"messages": []}, {"configurable": {}})

        mock_post.assert_not_called()
        graph.aupdate_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_closeout_on_cancel(self) -> None:
        graph = _make_graph(error=asyncio.CancelledError())
        wrapped = wrap_agent_with_error_closeout(graph)

        with patch(
            "agent.middleware.error_closeout.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            with pytest.raises(asyncio.CancelledError):
                await _drain(wrapped, {"messages": []}, SLACK_CONFIG)

        mock_post.assert_not_called()
        graph.aupdate_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_closeout_on_graph_interrupt(self) -> None:
        graph = _make_graph(error=GraphInterrupt())
        wrapped = wrap_agent_with_error_closeout(graph)

        with patch(
            "agent.middleware.error_closeout.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            with pytest.raises(GraphInterrupt):
                await _drain(wrapped, {"messages": []}, SLACK_CONFIG)

        mock_post.assert_not_called()
        graph.aupdate_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_closeout_on_success(self) -> None:
        graph = _make_graph(error=None)
        wrapped = wrap_agent_with_error_closeout(graph)

        with patch(
            "agent.middleware.error_closeout.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post:
            await _drain(wrapped, {"messages": []}, SLACK_CONFIG)

        mock_post.assert_not_called()
        graph.aupdate_state.assert_not_called()
