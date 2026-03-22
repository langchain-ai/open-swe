"""Tests for agent.tools.mcp_call."""

from unittest.mock import MagicMock, patch

import httpx
import pytest


class TestMcpCall:
    def test_successful_call(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "Search results here"}]},
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("agent.tools.mcp_call.MCP_SERVER_URL", "https://mcp.example.com/mcp"),
            patch("agent.tools.mcp_call.MCP_SERVER_API_KEY", "test-key"),
            patch("httpx.post", return_value=mock_response) as mock_post,
        ):
            from agent.tools.mcp_call import mcp_call

            result = mcp_call("search", {"query": "race condition", "platform": "ios"})

        assert result == "Search results here"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == "https://mcp.example.com/mcp"
        payload = call_kwargs[1]["json"]
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "search"
        assert payload["params"]["arguments"] == {"query": "race condition", "platform": "ios"}

    def test_missing_url_raises_value_error(self) -> None:
        with patch("agent.tools.mcp_call.MCP_SERVER_URL", None):
            from agent.tools.mcp_call import mcp_call

            with pytest.raises(ValueError, match="MCP_SERVER_URL environment variable is not set"):
                mcp_call("search", {"query": "test"})

    def test_mcp_error_response(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("agent.tools.mcp_call.MCP_SERVER_URL", "https://mcp.example.com/mcp"),
            patch("httpx.post", return_value=mock_response),
        ):
            from agent.tools.mcp_call import mcp_call

            result = mcp_call("nonexistent_tool", {})

        assert result == "MCP error: Method not found"

    def test_empty_content_response(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": []},
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("agent.tools.mcp_call.MCP_SERVER_URL", "https://mcp.example.com/mcp"),
            patch("httpx.post", return_value=mock_response),
        ):
            from agent.tools.mcp_call import mcp_call

            result = mcp_call("get_rca", {"id": "SWAT-421"})

        assert result == "No content returned from MCP server."

    def test_none_values_filtered_from_arguments(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("agent.tools.mcp_call.MCP_SERVER_URL", "https://mcp.example.com/mcp"),
            patch("httpx.post", return_value=mock_response) as mock_post,
        ):
            from agent.tools.mcp_call import mcp_call

            mcp_call("search", {"query": "test", "platform": None, "limit": 10})

        payload = mock_post.call_args[1]["json"]
        assert "platform" not in payload["params"]["arguments"]
        assert payload["params"]["arguments"] == {"query": "test", "limit": 10}

    def test_timeout_configuration(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("agent.tools.mcp_call.MCP_SERVER_URL", "https://mcp.example.com/mcp"),
            patch("agent.tools.mcp_call.MCP_SERVER_TIMEOUT", 60),
            patch("httpx.post", return_value=mock_response) as mock_post,
        ):
            from agent.tools.mcp_call import mcp_call

            mcp_call("search", {"query": "test"})

        assert mock_post.call_args[1]["timeout"] == 60

    def test_auth_header_set_when_api_key_present(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("agent.tools.mcp_call.MCP_SERVER_URL", "https://mcp.example.com/mcp"),
            patch("agent.tools.mcp_call.MCP_SERVER_API_KEY", "secret-key"),
            patch("httpx.post", return_value=mock_response) as mock_post,
        ):
            from agent.tools.mcp_call import mcp_call

            mcp_call("search", {"query": "test"})

        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret-key"

    def test_no_auth_header_when_api_key_absent(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("agent.tools.mcp_call.MCP_SERVER_URL", "https://mcp.example.com/mcp"),
            patch("agent.tools.mcp_call.MCP_SERVER_API_KEY", None),
            patch("httpx.post", return_value=mock_response) as mock_post,
        ):
            from agent.tools.mcp_call import mcp_call

            mcp_call("search", {"query": "test"})

        headers = mock_post.call_args[1]["headers"]
        assert "Authorization" not in headers

    def test_http_error_propagates(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Forbidden",
            request=MagicMock(),
            response=MagicMock(),
        )

        with (
            patch("agent.tools.mcp_call.MCP_SERVER_URL", "https://mcp.example.com/mcp"),
            patch("httpx.post", return_value=mock_response),
        ):
            from agent.tools.mcp_call import mcp_call

            with pytest.raises(httpx.HTTPStatusError):
                mcp_call("search", {"query": "test"})
