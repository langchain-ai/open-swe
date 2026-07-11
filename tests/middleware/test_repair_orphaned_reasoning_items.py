from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from agent.middleware.repair_orphaned_reasoning_items import (
    RepairOrphanedReasoningItemsMiddleware,
)


def _responses_model() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-5.6-sol",
        api_key="test",
        use_responses_api=True,
        store=False,
        include=["reasoning.encrypted_content"],
        output_version="responses/v1",
    )


def _make_request(messages: list[object], model: object) -> MagicMock:
    request = MagicMock()
    request.model = model
    request.messages = messages
    return request


async def _noop_handler(_req: object) -> object:
    return MagicMock()


def _orphaned_function_call_message() -> AIMessage:
    return AIMessage(
        content=[
            {
                "type": "function_call",
                "id": "fc_execute",
                "call_id": "call_execute",
                "name": "execute",
                "arguments": '{"command":"pwd"}',
            }
        ],
        tool_calls=[
            {
                "name": "execute",
                "args": {"command": "pwd"},
                "id": "call_execute",
                "type": "tool_call",
            }
        ],
    )


class TestRepairOrphanedReasoningItemsMiddleware:
    @pytest.mark.asyncio
    async def test_makes_orphaned_function_call_provider_valid(self) -> None:
        model = _responses_model()
        messages = [
            HumanMessage("test the todo middleware"),
            _orphaned_function_call_message(),
            ToolMessage(content="/workspace", tool_call_id="call_execute", name="execute"),
            HumanMessage("continue"),
        ]
        request = _make_request(messages, model)

        await RepairOrphanedReasoningItemsMiddleware().awrap_model_call(request, _noop_handler)

        payload = model._get_request_payload(request.messages)
        function_call_ids = {
            item.get("id")
            for item in payload["input"]
            if isinstance(item, dict) and item.get("type") == "function_call"
        }
        assert "fc_execute" not in function_call_ids
        assert not any(
            isinstance(item, dict) and item.get("type") == "function_call_output"
            for item in payload["input"]
        )

    @pytest.mark.asyncio
    async def test_preserves_paired_reasoning_and_function_call(self) -> None:
        model = _responses_model()
        paired = AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_reasoning",
                    "summary": [],
                    "encrypted_content": "encrypted",
                },
                {
                    "type": "function_call",
                    "id": "fc_execute",
                    "call_id": "call_execute",
                    "name": "execute",
                    "arguments": '{"command":"pwd"}',
                },
            ],
            tool_calls=[
                {
                    "name": "execute",
                    "args": {"command": "pwd"},
                    "id": "call_execute",
                    "type": "tool_call",
                }
            ],
        )
        tool_message = ToolMessage(content="/w", tool_call_id="call_execute", name="execute")
        request = _make_request([HumanMessage("hi"), paired, tool_message], model)

        await RepairOrphanedReasoningItemsMiddleware().awrap_model_call(request, _noop_handler)

        assert paired.content[0]["type"] == "reasoning"
        assert any(
            isinstance(block, dict) and block.get("type") == "function_call"
            for block in paired.content
        )
        assert paired.tool_calls
        assert tool_message in request.messages

    @pytest.mark.asyncio
    async def test_ignores_non_openai_responses_models(self) -> None:
        orphaned = _orphaned_function_call_message()
        request = _make_request([HumanMessage("hi"), orphaned], model=MagicMock())

        await RepairOrphanedReasoningItemsMiddleware().awrap_model_call(request, _noop_handler)

        assert any(
            isinstance(block, dict) and block.get("type") == "function_call"
            for block in orphaned.content
        )
        assert orphaned.tool_calls
