from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware.execute_output_cap import (
    _MAX_EXECUTE_RESULT_CHARS,
    ExecuteOutputCapMiddleware,
)


def _make_request(name: str) -> MagicMock:
    request = MagicMock()
    request.tool_call = {"name": name, "args": {"command": "gh api ..."}, "id": "c1"}
    return request


class TestExecuteOutputCapMiddleware:
    @pytest.mark.asyncio
    async def test_truncates_oversized_execute_output(self) -> None:
        big = "A" * (_MAX_EXECUTE_RESULT_CHARS + 500_000)
        result = ToolMessage(content=big, tool_call_id="c1")

        async def handler(_req: object) -> ToolMessage:
            return result

        out = await ExecuteOutputCapMiddleware().awrap_tool_call(_make_request("execute"), handler)

        assert isinstance(out, ToolMessage)
        assert len(out.content) < len(big)
        assert "output truncated" in out.content

    @pytest.mark.asyncio
    async def test_leaves_small_output_untouched(self) -> None:
        small = "ok"
        result = ToolMessage(content=small, tool_call_id="c1")

        async def handler(_req: object) -> ToolMessage:
            return result

        out = await ExecuteOutputCapMiddleware().awrap_tool_call(_make_request("execute"), handler)

        assert out.content == small

    @pytest.mark.asyncio
    async def test_leaves_non_execute_tools_untouched(self) -> None:
        big = "A" * (_MAX_EXECUTE_RESULT_CHARS + 500_000)
        result = ToolMessage(content=big, tool_call_id="c1")

        async def handler(_req: object) -> ToolMessage:
            return result

        out = await ExecuteOutputCapMiddleware().awrap_tool_call(
            _make_request("read_file"), handler
        )

        assert out.content == big

    @pytest.mark.asyncio
    async def test_caps_text_content_blocks(self) -> None:
        big = "A" * (_MAX_EXECUTE_RESULT_CHARS + 500_000)
        blocks = [{"type": "text", "text": big}, {"type": "other"}]
        result = ToolMessage(content=blocks, tool_call_id="c1")

        async def handler(_req: object) -> ToolMessage:
            return result

        out = await ExecuteOutputCapMiddleware().awrap_tool_call(_make_request("execute"), handler)

        assert "output truncated" in out.content[0]["text"]
        assert out.content[1] == {"type": "other"}
