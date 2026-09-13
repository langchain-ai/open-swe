"""Unit tests for SanitizeToolInputsMiddleware.

Guards against the regression where the LLM generates a string value for an
integer field in read_file (e.g. offset='1, 80'), causing a Pydantic
ValidationError and an unnecessary retry.
"""

from importlib import import_module
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware.sanitize_tool_inputs import (
    SanitizeToolInputsMiddleware,
    _coerce_int,
    _sanitize_read_file_args,
)

_MCPTool = import_module("agent.mcp.runtime")._MCPTool


class TestCoerceInt:
    def test_already_int_passes_through(self) -> None:
        assert _coerce_int(1) == 1

    def test_none_passes_through(self) -> None:
        assert _coerce_int(None) is None

    def test_extracts_leading_integer_from_comma_string(self) -> None:
        # Production trace 1: offset='1, 80'
        assert _coerce_int("1, 80") == 1

    def test_extracts_leading_integer_from_embedded_json(self) -> None:
        # Production trace 2: offset='170, "limit": 60'
        assert _coerce_int('170, "limit": 60') == 170

    def test_extracts_leading_integer_from_trailing_comma(self) -> None:
        # Production trace 3: offset='1504, '
        assert _coerce_int("1504, ") == 1504

    def test_returns_none_when_no_digits(self) -> None:
        assert _coerce_int("abc") is None

    def test_handles_leading_whitespace(self) -> None:
        assert _coerce_int("  42, extra") == 42


class TestSanitizeReadFileArgs:
    def test_coerces_offset_string_to_int(self) -> None:
        args = {"file_path": "foo.ts", "offset": "1, 80", "limit": 80}
        result = _sanitize_read_file_args(args)
        assert result["offset"] == 1
        assert result["limit"] == 80
        assert result["file_path"] == "foo.ts"

    def test_coerces_offset_with_embedded_json(self) -> None:
        args = {"file_path": "bar.tsx", "offset": '170, "limit": 60', "limit": 60}
        result = _sanitize_read_file_args(args)
        assert result["offset"] == 170

    def test_coerces_offset_with_trailing_comma(self) -> None:
        args = {"file_path": "baz.go", "offset": "1504, ", "limit": 200}
        result = _sanitize_read_file_args(args)
        assert result["offset"] == 1504

    def test_int_offset_unchanged(self) -> None:
        args = {"file_path": "foo.ts", "offset": 42, "limit": 80}
        result = _sanitize_read_file_args(args)
        assert result["offset"] == 42

    def test_missing_offset_unchanged(self) -> None:
        args = {"file_path": "foo.ts"}
        result = _sanitize_read_file_args(args)
        assert "offset" not in result

    def test_uncoercible_offset_passed_through_unchanged(self) -> None:
        # If no digits at all, we leave the value alone so ToolErrorMiddleware handles it.
        args = {"file_path": "foo.ts", "offset": "bad"}
        result = _sanitize_read_file_args(args)
        assert result["offset"] == "bad"

    def test_does_not_mutate_original_dict(self) -> None:
        args = {"file_path": "foo.ts", "offset": "1, 80"}
        _ = _sanitize_read_file_args(args)
        assert args["offset"] == "1, 80"


@pytest.mark.asyncio
async def test_sanitizes_linear_mcp_arguments_before_dispatch() -> None:
    args = {
        "issueId": "ENT-1642",
        "body": "comment",
        "statusUpdateType": "project",
        "statusUpdateId": "",
        "projectId": "",
    }
    request = ToolCallRequest(
        tool_call={"name": "mcp_linear_save_comment", "args": args, "id": "call-1"},
        tool=object.__new__(_MCPTool),
        state={},
        runtime=MagicMock(),
    )
    received: list[dict[str, object]] = []

    async def handler(dispatched: ToolCallRequest) -> ToolMessage:
        received.append(dispatched.tool_call["args"])
        return ToolMessage(content="ok", tool_call_id="call-1")

    await SanitizeToolInputsMiddleware().awrap_tool_call(request, handler)

    assert received == [{"issueId": "ENT-1642", "body": "comment"}]
