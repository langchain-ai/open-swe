from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.repair_orphaned_tool_calls import (
    INTERRUPTED_TOOL_RECOVERY,
    RepairOrphanedToolCallsMiddleware,
)


def _make_request(messages: list[object]) -> MagicMock:
    request = MagicMock()
    request.model = MagicMock()
    request.messages = messages
    return request


def _ai_with_tool_call(call_id: str, name: str = "execute") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"command": "ls"}, "id": call_id, "type": "tool_call"}],
    )


class TestRepairOrphanedToolCallsMiddleware:
    def test_inserts_synthetic_result_for_orphaned_tool_call(self) -> None:
        ai = _ai_with_tool_call("call_1")
        follow_up = HumanMessage(content="continue")
        request = _make_request([HumanMessage(content="hi"), ai, follow_up])
        response = MagicMock()

        result = RepairOrphanedToolCallsMiddleware().wrap_model_call(request, lambda req: response)

        assert result is response
        messages = request.messages
        assert len(messages) == 4
        synthetic = messages[2]
        assert isinstance(synthetic, ToolMessage)
        assert synthetic.tool_call_id == "call_1"
        assert synthetic.status == "error"
        assert messages[3] is follow_up
        payload = json.loads(synthetic.content)
        assert payload["recovery"] == INTERRUPTED_TOOL_RECOVERY
        assert payload["name"] == "execute"

    def test_leaves_satisfied_tool_calls_untouched(self) -> None:
        ai = _ai_with_tool_call("call_1")
        tool = ToolMessage(content="done", tool_call_id="call_1")
        original = [HumanMessage(content="hi"), ai, tool]
        request = _make_request(list(original))

        RepairOrphanedToolCallsMiddleware().wrap_model_call(request, lambda req: MagicMock())

        assert request.messages == original

    def test_repairs_multiple_orphans_on_one_message(self) -> None:
        ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "execute", "args": {}, "id": "call_1", "type": "tool_call"},
                {"name": "grep", "args": {}, "id": "call_2", "type": "tool_call"},
            ],
        )
        request = _make_request([ai])

        RepairOrphanedToolCallsMiddleware().wrap_model_call(request, lambda req: MagicMock())

        messages = request.messages
        assert [getattr(m, "tool_call_id", None) for m in messages[1:]] == ["call_1", "call_2"]
        assert all(isinstance(m, ToolMessage) for m in messages[1:])

    def test_partial_repair_keeps_existing_result(self) -> None:
        ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "execute", "args": {}, "id": "call_1", "type": "tool_call"},
                {"name": "grep", "args": {}, "id": "call_2", "type": "tool_call"},
            ],
        )
        tool = ToolMessage(content="done", tool_call_id="call_1")
        request = _make_request([ai, tool])

        RepairOrphanedToolCallsMiddleware().wrap_model_call(request, lambda req: MagicMock())

        synthetic = [
            m for m in request.messages if isinstance(m, ToolMessage) and m.status == "error"
        ]
        assert len(synthetic) == 1
        assert synthetic[0].tool_call_id == "call_2"

    @pytest.mark.asyncio
    async def test_async_inserts_synthetic_result(self) -> None:
        ai = _ai_with_tool_call("call_1")
        request = _make_request([ai, HumanMessage(content="hi")])
        response = MagicMock()

        async def handler(req: object) -> object:
            assert req is request
            return response

        result = await RepairOrphanedToolCallsMiddleware().awrap_model_call(request, handler)

        assert result is response
        assert isinstance(request.messages[1], ToolMessage)
        assert request.messages[1].tool_call_id == "call_1"
