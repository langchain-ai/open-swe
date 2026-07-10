"""Regression tests: web_search / http_request cap oversized tool output."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import httpx
import pytest

from agent.tools.http_request import HTTP_REQUEST_MAX_CHARS, http_request
from agent.tools.web_search import (
    WEB_SEARCH_CONTENT_MAX_CHARS,
    WEB_SEARCH_MAX_CHARS,
    web_search,
)

http_request_mod = importlib.import_module("agent.tools.http_request")


async def test_web_search_truncates_oversized_results() -> None:
    class _FakeExa:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def search_and_contents(self, *_args, **kwargs) -> str:
            assert kwargs["text"] == {"max_characters": WEB_SEARCH_CONTENT_MAX_CHARS}
            return "x" * 700_000

    with (
        patch.dict("os.environ", {"EXA_API_KEY": "key"}),
        patch.dict("sys.modules", {"exa_py": type("m", (), {"Exa": _FakeExa})}),
    ):
        result = await web_search("query")

    assert result["success"] is True
    assert len(result["results"]) <= WEB_SEARCH_MAX_CHARS + 100
    assert "truncated" in result["results"]


async def test_http_request_truncates_oversized_text_body() -> None:
    oversized = "y" * 700_000

    async def _fake_request(_client, _method, _url, **_kwargs):
        response = httpx.Response(
            200,
            request=httpx.Request("GET", "https://example.com"),
            text=oversized,
        )
        return response, None

    with patch.object(http_request_mod, "_request_with_safe_redirects", side_effect=_fake_request):
        result = await http_request("https://example.com")

    assert result["success"] is True
    assert isinstance(result["content"], str)
    assert len(result["content"]) <= HTTP_REQUEST_MAX_CHARS + 100
    assert "truncated" in result["content"]


@pytest.mark.parametrize("cap", [WEB_SEARCH_MAX_CHARS, HTTP_REQUEST_MAX_CHARS])
def test_caps_are_bounded(cap: int) -> None:
    assert 0 < cap <= 100_000
