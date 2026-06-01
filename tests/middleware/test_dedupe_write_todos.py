"""Unit tests for DedupeWriteTodosMiddleware.

Guards against the regression where the reviewer agent repeatedly rewrites
the same todo list with cosmetic phrasing changes, inflating trajectory
length and token cost without changing any decision.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware.dedupe_write_todos import DedupeWriteTodosMiddleware


def _request(todos: list[dict], state: dict, tool_call_id: str = "tc1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "write_todos", "args": {"todos": todos}, "id": tool_call_id},
        tool=MagicMock(),
        state=state,
        runtime=MagicMock(),
    )


def _persisting_handler(state: dict):
    def handler(request: ToolCallRequest) -> ToolMessage:
        state["todos"] = list(request.tool_call["args"]["todos"])
        return ToolMessage(
            f"Updated todo list to {state['todos']}",
            tool_call_id=request.tool_call["id"],
            name="write_todos",
        )

    return handler


class TestDedupeWriteTodosMiddleware:
    def test_first_call_persists(self) -> None:
        middleware = DedupeWriteTodosMiddleware()
        state: dict = {}
        todos = [{"content": "Read diff", "status": "in_progress"}]
        result = middleware.wrap_tool_call(_request(todos, state), _persisting_handler(state))
        assert isinstance(result, ToolMessage)
        assert state["todos"] == todos

    def test_identical_repeat_is_noop(self) -> None:
        middleware = DedupeWriteTodosMiddleware()
        todos = [
            {"content": "Read diff", "status": "in_progress"},
            {"content": "Grep callsites", "status": "pending"},
        ]
        state: dict = {"todos": todos}
        handler = MagicMock()
        result = middleware.wrap_tool_call(_request(todos, state), handler)
        handler.assert_not_called()
        assert isinstance(result, ToolMessage)
        assert "unchanged" in result.content

    def test_cosmetic_phrasing_change_is_noop(self) -> None:
        middleware = DedupeWriteTodosMiddleware()
        prior = [{"content": "Read diff", "status": "in_progress"}]
        new = [{"content": "  read DIFF  ", "status": "IN_PROGRESS"}]
        state: dict = {"todos": prior}
        handler = MagicMock()
        result = middleware.wrap_tool_call(_request(new, state), handler)
        handler.assert_not_called()
        assert isinstance(result, ToolMessage)

    def test_reordered_list_is_noop(self) -> None:
        middleware = DedupeWriteTodosMiddleware()
        prior = [
            {"content": "A", "status": "pending"},
            {"content": "B", "status": "pending"},
        ]
        new = [
            {"content": "B", "status": "pending"},
            {"content": "A", "status": "pending"},
        ]
        state: dict = {"todos": prior}
        handler = MagicMock()
        result = middleware.wrap_tool_call(_request(new, state), handler)
        handler.assert_not_called()
        assert isinstance(result, ToolMessage)

    def test_status_change_persists(self) -> None:
        middleware = DedupeWriteTodosMiddleware()
        prior = [{"content": "Read diff", "status": "in_progress"}]
        new = [{"content": "Read diff", "status": "completed"}]
        state: dict = {"todos": prior}
        result = middleware.wrap_tool_call(_request(new, state), _persisting_handler(state))
        assert isinstance(result, ToolMessage)
        assert state["todos"] == new

    def test_new_item_persists(self) -> None:
        middleware = DedupeWriteTodosMiddleware()
        prior = [{"content": "Read diff", "status": "in_progress"}]
        new = [
            {"content": "Read diff", "status": "in_progress"},
            {"content": "Grep callsites", "status": "pending"},
        ]
        state: dict = {"todos": prior}
        result = middleware.wrap_tool_call(_request(new, state), _persisting_handler(state))
        assert isinstance(result, ToolMessage)
        assert state["todos"] == new

    def test_non_write_todos_call_passes_through(self) -> None:
        middleware = DedupeWriteTodosMiddleware()
        state: dict = {"todos": [{"content": "X", "status": "pending"}]}
        request = ToolCallRequest(
            tool_call={"name": "read_file", "args": {"file_path": "a.py"}, "id": "tc1"},
            tool=MagicMock(),
            state=state,
            runtime=MagicMock(),
        )
        sentinel = ToolMessage("ok", tool_call_id="tc1")
        handler = MagicMock(return_value=sentinel)
        result = middleware.wrap_tool_call(request, handler)
        handler.assert_called_once()
        assert result is sentinel

    def test_five_identical_calls_only_first_persists(self) -> None:
        middleware = DedupeWriteTodosMiddleware()
        state: dict = {}
        todos = [
            {"content": "Read diff", "status": "in_progress"},
            {"content": "Grep callsites", "status": "pending"},
            {"content": "Publish review", "status": "pending"},
        ]
        call_count = {"n": 0}

        def handler(request: ToolCallRequest) -> ToolMessage:
            call_count["n"] += 1
            state["todos"] = list(request.tool_call["args"]["todos"])
            return ToolMessage("ok", tool_call_id=request.tool_call["id"], name="write_todos")

        for _ in range(5):
            middleware.wrap_tool_call(_request(todos, state), handler)
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_async_identical_repeat_is_noop(self) -> None:
        middleware = DedupeWriteTodosMiddleware()
        todos = [{"content": "Read diff", "status": "in_progress"}]
        state: dict = {"todos": todos}

        async def handler(_request: ToolCallRequest) -> ToolMessage:
            raise AssertionError("handler should not be invoked for no-op call")

        result = await middleware.awrap_tool_call(_request(todos, state), handler)
        assert isinstance(result, ToolMessage)
        assert "unchanged" in result.content
