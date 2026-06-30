"""Tests for CapToolResultsMiddleware truncation behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware.cap_tool_results import CapToolResultsMiddleware


def _request(name: str = "web_search") -> Any:
    return SimpleNamespace(
        tool_call={"name": name, "args": {}, "id": "call-1"},
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "t1"}}),
    )


def _oversized(head: str, tail: str, filler_chars: int) -> str:
    return head + ("M" * filler_chars) + tail


async def test_async_caps_oversized_tool_message_preserving_head_and_tail() -> None:
    mw = CapToolResultsMiddleware()
    head = "H" * mw.HEAD_CHARS
    tail = "T" * mw.TAIL_CHARS
    middle_size = mw.MAX_TOOL_RESULT_CHARS  # ensures total > cap
    content = _oversized(head, tail, middle_size)
    original_len = len(content)

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(
            content=content,
            tool_call_id="call-1",
            status="success",
            name="web_search",
            artifact={"keep": "me"},
        )

    result = await mw.awrap_tool_call(_request(), handler)

    assert isinstance(result, ToolMessage)
    omitted = original_len - mw.HEAD_CHARS - mw.TAIL_CHARS
    marker = f"\n\n... [omitted {omitted} chars by CapToolResultsMiddleware] ...\n\n"
    assert len(result.content) == mw.HEAD_CHARS + mw.TAIL_CHARS + len(marker)
    assert result.content.startswith(head)
    assert result.content.endswith(tail)
    assert marker in result.content
    assert result.tool_call_id == "call-1"
    assert result.name == "web_search"
    assert result.status == "success"
    assert result.artifact == {"keep": "me"}


def test_sync_caps_oversized_tool_message() -> None:
    mw = CapToolResultsMiddleware()
    head = "A" * mw.HEAD_CHARS
    tail = "Z" * mw.TAIL_CHARS
    content = _oversized(head, tail, mw.MAX_TOOL_RESULT_CHARS)
    original_len = len(content)

    def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content=content, tool_call_id="call-1", name="execute")

    result = mw.wrap_tool_call(_request("execute"), handler)

    assert isinstance(result, ToolMessage)
    omitted = original_len - mw.HEAD_CHARS - mw.TAIL_CHARS
    marker = f"\n\n... [omitted {omitted} chars by CapToolResultsMiddleware] ...\n\n"
    assert len(result.content) == mw.HEAD_CHARS + mw.TAIL_CHARS + len(marker)
    assert result.content[: mw.HEAD_CHARS] == head
    assert result.content[-mw.TAIL_CHARS :] == tail


async def test_under_cap_passes_through_untouched() -> None:
    mw = CapToolResultsMiddleware()
    content = "small" * 100

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content=content, tool_call_id="call-1", name="web_search")

    result = await mw.awrap_tool_call(_request(), handler)
    assert isinstance(result, ToolMessage)
    assert result.content == content


async def test_constructor_override_applies() -> None:
    mw = CapToolResultsMiddleware(max_tool_result_chars=100, head_chars=10, tail_chars=10)
    content = "x" * 500

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content=content, tool_call_id="call-1", name="web_search")

    result = await mw.awrap_tool_call(_request(), handler)
    assert isinstance(result, ToolMessage)
    marker = f"\n\n... [omitted {500 - 20} chars by CapToolResultsMiddleware] ...\n\n"
    assert len(result.content) == 20 + len(marker)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-vvv"])
