"""Unit tests for DedupeReadFileMiddleware.

Guards against the regression where the reviewer agent issues identical
`read_file(file_path, offset, limit)` calls many times within one
invocation, inflating the trajectory without producing new information.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware.dedupe_read_file import DedupeReadFileMiddleware


@dataclass
class _FakeRequest:
    tool_call: dict[str, Any]
    tool: Any = None
    state: Any = None
    runtime: Any = None


def _make_request(call_id: str, **args: Any) -> _FakeRequest:
    return _FakeRequest(
        tool_call={"name": "read_file", "args": args, "id": call_id},
    )


def _make_result(call_id: str, content: str) -> ToolMessage:
    return ToolMessage(
        content=content,
        name="read_file",
        tool_call_id=call_id,
        status="success",
    )


class TestDedupeReadFileMiddleware:
    def test_first_call_hits_handler_and_is_cached(self) -> None:
        mw = DedupeReadFileMiddleware()
        calls: list[str] = []

        def handler(req: _FakeRequest) -> ToolMessage:
            calls.append(req.tool_call["id"])
            return _make_result(req.tool_call["id"], "FILE CONTENT")

        result = mw.wrap_tool_call(
            _make_request("c1", file_path="a.py", offset=0, limit=100), handler
        )  # type: ignore[arg-type]
        assert isinstance(result, ToolMessage)
        assert calls == ["c1"]
        assert result.content == "FILE CONTENT"

    def test_three_identical_calls_only_first_hits_handler(self) -> None:
        mw = DedupeReadFileMiddleware()
        calls: list[str] = []

        def handler(req: _FakeRequest) -> ToolMessage:
            calls.append(req.tool_call["id"])
            return _make_result(req.tool_call["id"], "FILE CONTENT")

        args = {"file_path": "a.py", "offset": 0, "limit": 100}
        r1 = mw.wrap_tool_call(_make_request("c1", **args), handler)  # type: ignore[arg-type]
        r2 = mw.wrap_tool_call(_make_request("c2", **args), handler)  # type: ignore[arg-type]
        r3 = mw.wrap_tool_call(_make_request("c3", **args), handler)  # type: ignore[arg-type]

        assert calls == ["c1"], "handler must only run once for identical (path, offset, limit)"
        assert isinstance(r1, ToolMessage) and r1.content == "FILE CONTENT"
        assert isinstance(r2, ToolMessage)
        assert isinstance(r3, ToolMessage)
        # Cache hits get a warning prefix that names the prior call_id.
        assert "WARNING" in r2.content and "c1" in r2.content
        assert "WARNING" in r3.content and "c1" in r3.content
        # Original content still present, and tool_call_id is the *new* call's id.
        assert "FILE CONTENT" in r2.content
        assert r2.tool_call_id == "c2"
        assert r3.tool_call_id == "c3"

    def test_different_offsets_are_distinct_keys(self) -> None:
        mw = DedupeReadFileMiddleware()
        calls: list[str] = []

        def handler(req: _FakeRequest) -> ToolMessage:
            calls.append(req.tool_call["id"])
            return _make_result(req.tool_call["id"], f"content-{req.tool_call['id']}")

        mw.wrap_tool_call(_make_request("c1", file_path="a.py", offset=0, limit=100), handler)  # type: ignore[arg-type]
        mw.wrap_tool_call(_make_request("c2", file_path="a.py", offset=100, limit=100), handler)  # type: ignore[arg-type]
        mw.wrap_tool_call(_make_request("c3", file_path="b.py", offset=0, limit=100), handler)  # type: ignore[arg-type]
        assert calls == ["c1", "c2", "c3"]

    def test_non_read_file_calls_pass_through_uncached(self) -> None:
        mw = DedupeReadFileMiddleware()
        calls: list[str] = []

        def handler(req: _FakeRequest) -> ToolMessage:
            calls.append(req.tool_call["id"])
            return ToolMessage(
                content="ok",
                name=req.tool_call["name"],
                tool_call_id=req.tool_call["id"],
                status="success",
            )

        req = _FakeRequest(
            tool_call={"name": "add_finding", "args": {"file": "a.py"}, "id": "c1"},
        )
        mw.wrap_tool_call(req, handler)  # type: ignore[arg-type]
        mw.wrap_tool_call(req, handler)  # type: ignore[arg-type]
        assert calls == ["c1", "c1"], "non-read_file calls must not be deduped"

    def test_error_results_are_not_cached(self) -> None:
        mw = DedupeReadFileMiddleware()
        calls: list[str] = []

        def handler(req: _FakeRequest) -> ToolMessage:
            calls.append(req.tool_call["id"])
            return ToolMessage(
                content="boom",
                name="read_file",
                tool_call_id=req.tool_call["id"],
                status="error",
            )

        args = {"file_path": "a.py", "offset": 0, "limit": 100}
        mw.wrap_tool_call(_make_request("c1", **args), handler)  # type: ignore[arg-type]
        mw.wrap_tool_call(_make_request("c2", **args), handler)  # type: ignore[arg-type]
        assert calls == ["c1", "c2"], "errored reads must be retryable"

    @pytest.mark.asyncio
    async def test_async_wrap_dedupes(self) -> None:
        mw = DedupeReadFileMiddleware()
        calls: list[str] = []

        async def handler(req: _FakeRequest) -> ToolMessage:
            calls.append(req.tool_call["id"])
            return _make_result(req.tool_call["id"], "ASYNC CONTENT")

        args = {"file_path": "a.py", "offset": 0, "limit": 100}
        await mw.awrap_tool_call(_make_request("c1", **args), handler)  # type: ignore[arg-type]
        result = await mw.awrap_tool_call(_make_request("c2", **args), handler)  # type: ignore[arg-type]
        await mw.awrap_tool_call(_make_request("c3", **args), handler)  # type: ignore[arg-type]
        assert calls == ["c1"]
        assert isinstance(result, ToolMessage)
        assert "WARNING" in result.content
