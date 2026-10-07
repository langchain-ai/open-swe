from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from openswe.middleware.repair_orphaned_tool_calls import (
    RepairOrphanedToolCallsMiddleware,
)


def _make_request(messages: list[object]) -> ModelRequest[None]:
    request = MagicMock()
    request.model = MagicMock()
    request.messages = messages
    return cast(ModelRequest[None], request)


def _ai_with_tool_call(call_id: str, name: str = "execute") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"command": "ls"}, "id": call_id, "type": "tool_call"}],
    )


async def _noop_handler(_req: ModelRequest[None]) -> ModelResponse[Any]:
    return cast(ModelResponse[Any], MagicMock())


class TestRepairOrphanedToolCallsMiddleware:
    @pytest.mark.asyncio
    async def test_partial_repair_keeps_existing_result(self) -> None:
        ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "execute", "args": {}, "id": "call_1", "type": "tool_call"},
                {"name": "grep", "args": {}, "id": "call_2", "type": "tool_call"},
            ],
        )
        tool = ToolMessage(content="done", tool_call_id="call_1")
        request = _make_request([ai, tool])

        await RepairOrphanedToolCallsMiddleware().awrap_model_call(request, _noop_handler)

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

        async def handler(req: ModelRequest[None]) -> ModelResponse[Any]:
            assert req is request
            return cast(ModelResponse[Any], response)

        result = await RepairOrphanedToolCallsMiddleware().awrap_model_call(request, handler)

        assert result is response
        assert isinstance(request.messages[1], ToolMessage)
        assert request.messages[1].tool_call_id == "call_1"
