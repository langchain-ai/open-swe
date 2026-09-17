from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.repair_malformed_tool_calls import RepairMalformedToolCallsMiddleware


def _make_request(messages: list[object]) -> ModelRequest[None]:
    request = MagicMock()
    request.messages = messages
    return cast(ModelRequest[None], request)


async def _noop_handler(_req: ModelRequest[None]) -> ModelResponse[Any]:
    return cast(ModelResponse[Any], MagicMock())


def _malformed_ai_message() -> AIMessage:
    return AIMessage.model_construct(
        content="",
        tool_calls=[{"name": "execute", "args": '{"command":', "id": "call-1"}],
        invalid_tool_calls=[],
    )


class TestRepairMalformedToolCallsMiddleware:
    @pytest.mark.asyncio
    async def test_repairs_standard_tool_calls_and_inserts_error(self) -> None:
        message = _malformed_ai_message()
        request = _make_request([message, HumanMessage(content="continue")])

        await RepairMalformedToolCallsMiddleware().awrap_model_call(request, _noop_handler)

        assert message.tool_calls[0]["args"] == {}
        assert isinstance(request.messages[1], ToolMessage)
        assert request.messages[1].tool_call_id == "call-1"
        assert "malformed" in request.messages[1].content

    @pytest.mark.asyncio
    async def test_normalizes_valid_json_and_replaces_non_object(self) -> None:
        invalid = {"type": "invalid_tool_call", "args": "[1]", "id": "call-2"}
        message = AIMessage.model_construct(
            content="",
            tool_calls=[{"name": "one", "args": '{"value": 1}', "id": "call-1"}],
            invalid_tool_calls=[invalid],
        )
        request = _make_request([message])

        await RepairMalformedToolCallsMiddleware().awrap_model_call(request, _noop_handler)

        assert message.tool_calls[0]["args"] == {"value": 1}
        assert invalid["args"] == {}
        assert len(request.messages) == 2
        assert request.messages[1].tool_call_id == "call-2"

    @pytest.mark.asyncio
    async def test_repairs_non_standard_invalid_tool_call_block(self) -> None:
        block = {
            "type": "non_standard",
            "value": {
                "type": "invalid_tool_call",
                "arguments": "not-json",
                "id": "call-3",
            },
        }
        message = AIMessage(content=[block])
        request = _make_request([message])

        await RepairMalformedToolCallsMiddleware().awrap_model_call(request, _noop_handler)

        assert block["value"]["arguments"] == {}
        assert request.messages[1].tool_call_id == "call-3"

    @pytest.mark.asyncio
    async def test_leaves_valid_and_unrelated_messages_unchanged(self) -> None:
        message = AIMessage(
            content="ok",
            tool_calls=[{"name": "execute", "args": {"command": "ls"}, "id": "call-1"}],
        )
        original = [HumanMessage(content="hi"), message]
        request = _make_request(list(original))

        await RepairMalformedToolCallsMiddleware().awrap_model_call(request, _noop_handler)

        assert request.messages == original
