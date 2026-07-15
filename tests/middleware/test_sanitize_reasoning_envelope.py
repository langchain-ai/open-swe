from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.sanitize_reasoning_envelope import (
    SanitizeReasoningEnvelopeMiddleware,
    strip_reasoning_envelope,
)


class TestStripReasoningEnvelope:
    def test_strips_leading_envelope_and_keeps_markdown(self) -> None:
        text = '{"reasoning":"planning the answer","type":"reasoning"} # Done\n\nAll set.'
        assert strip_reasoning_envelope(text) == "# Done\n\nAll set."

    def test_strips_envelope_when_it_is_entire_content(self) -> None:
        text = '{"reasoning":"thinking","type":"reasoning"}'
        assert strip_reasoning_envelope(text) == ""

    def test_strips_envelope_keyed_only_by_reasoning(self) -> None:
        text = '{"reasoning":"thinking"}\nActual answer.'
        assert strip_reasoning_envelope(text) == "Actual answer."

    def test_leaves_plain_markdown_untouched(self) -> None:
        text = "# Title\n\nJust a normal answer."
        assert strip_reasoning_envelope(text) == text

    def test_leaves_unrelated_leading_json_untouched(self) -> None:
        text = '{"status":"ok"} rest'
        assert strip_reasoning_envelope(text) == text

    def test_leaves_malformed_json_untouched(self) -> None:
        text = '{"reasoning": not json} answer'
        assert strip_reasoning_envelope(text) == text


class TestSanitizeReasoningEnvelopeMiddleware:
    @pytest.mark.asyncio
    async def test_strips_envelope_from_model_output(self) -> None:
        message = AIMessage(
            content='{"reasoning":"plan","type":"reasoning"} Final markdown answer.'
        )
        request = MagicMock()
        response = MagicMock()
        response.result = [HumanMessage(content="hi"), message]

        async def handler(_req: object) -> object:
            return response

        result = await SanitizeReasoningEnvelopeMiddleware().awrap_model_call(request, handler)

        assert result is response
        assert message.content == "Final markdown answer."

    @pytest.mark.asyncio
    async def test_leaves_clean_output_untouched(self) -> None:
        message = AIMessage(content="Clean answer.")
        request = MagicMock()
        response = MagicMock()
        response.result = [message]

        async def handler(_req: object) -> object:
            return response

        await SanitizeReasoningEnvelopeMiddleware().awrap_model_call(request, handler)

        assert message.content == "Clean answer."
