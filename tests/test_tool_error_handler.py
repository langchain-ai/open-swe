from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware.tool_error_handler import (
    ToolErrorMiddleware,
    _extract_tool_name,
    _get_name,
    _get_tool_call_id,
    _to_error_payload,
)


# -- _get_name ------------------------------------------------------------


def test_get_name_from_string():
    assert _get_name("my_tool") == "my_tool"


def test_get_name_from_dict():
    assert _get_name({"name": "my_tool"}) == "my_tool"


def test_get_name_from_object():
    obj = MagicMock()
    obj.name = "my_tool"
    assert _get_name(obj) == "my_tool"


def test_get_name_returns_none_for_empty():
    assert _get_name(None) is None
    assert _get_name("") is None
    assert _get_name({}) is None


def test_get_name_returns_none_for_non_string_name():
    assert _get_name({"name": 42}) is None


# -- _extract_tool_name ---------------------------------------------------


def test_extract_tool_name_from_tool_call():
    req = MagicMock(spec=ToolCallRequest)
    req.tool_call = {"name": "http_request"}
    req.tool_name = None
    req.name = None
    assert _extract_tool_name(req) == "http_request"


def test_extract_tool_name_from_tool_name_attr():
    req = MagicMock(spec=ToolCallRequest)
    req.tool_call = {}
    req.tool_name = "fetch_url"
    req.name = None
    assert _extract_tool_name(req) == "fetch_url"


def test_extract_tool_name_returns_none():
    assert _extract_tool_name(None) is None


# -- _to_error_payload ----------------------------------------------------


def test_to_error_payload_basic():
    payload = _to_error_payload(ValueError("bad value"))
    assert payload["error"] == "bad value"
    assert payload["error_type"] == "ValueError"
    assert payload["status"] == "error"
    assert "name" not in payload


def test_to_error_payload_with_tool_name():
    req = MagicMock(spec=ToolCallRequest)
    req.tool_call = {"name": "http_request"}
    req.tool_name = None
    req.name = None
    payload = _to_error_payload(RuntimeError("oops"), req)
    assert payload["name"] == "http_request"


# -- _get_tool_call_id ---------------------------------------------------


def test_get_tool_call_id_from_dict():
    req = MagicMock(spec=ToolCallRequest)
    req.tool_call = {"id": "call_abc123"}
    assert _get_tool_call_id(req) == "call_abc123"


def test_get_tool_call_id_missing():
    req = MagicMock(spec=ToolCallRequest)
    req.tool_call = {}
    assert _get_tool_call_id(req) is None


def test_get_tool_call_id_non_dict():
    """When tool_call is not a dict, should return None."""
    req = MagicMock(spec=ToolCallRequest)
    req.tool_call = "not-a-dict"
    assert _get_tool_call_id(req) is None


# -- ToolErrorMiddleware.wrap_tool_call -----------------------------------


def test_wrap_tool_call_success():
    middleware = ToolErrorMiddleware()
    expected = ToolMessage(content="ok", tool_call_id="call_1")

    result = middleware.wrap_tool_call(MagicMock(), lambda req: expected)
    assert result == expected


def test_wrap_tool_call_catches_exception():
    middleware = ToolErrorMiddleware()
    req = MagicMock(spec=ToolCallRequest)
    req.tool_call = {"id": "call_1", "name": "bad_tool"}
    req.tool_name = None
    req.name = None

    def handler(r):
        raise RuntimeError("boom")

    result = middleware.wrap_tool_call(req, handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    data = json.loads(result.content)
    assert data["error"] == "boom"
    assert data["error_type"] == "RuntimeError"
    assert data["name"] == "bad_tool"


# -- ToolErrorMiddleware.awrap_tool_call ----------------------------------


@pytest.mark.asyncio
async def test_awrap_tool_call_success():
    middleware = ToolErrorMiddleware()
    expected = ToolMessage(content="ok", tool_call_id="call_1")

    result = await middleware.awrap_tool_call(MagicMock(), AsyncMock(return_value=expected))
    assert result == expected


@pytest.mark.asyncio
async def test_awrap_tool_call_catches_exception():
    middleware = ToolErrorMiddleware()
    req = MagicMock(spec=ToolCallRequest)
    req.tool_call = {"id": "call_2", "name": "async_bad"}
    req.tool_name = None
    req.name = None

    async def handler(r):
        raise ValueError("async boom")

    result = await middleware.awrap_tool_call(req, handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    data = json.loads(result.content)
    assert data["error"] == "async boom"
    assert data["error_type"] == "ValueError"
