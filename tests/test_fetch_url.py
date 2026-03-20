from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from agent.tools.fetch_url import fetch_url


def test_fetch_url_success():
    with patch("agent.tools.fetch_url.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = "<h1>Hello</h1><p>World</p>"
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fetch_url("https://example.com")
        assert "markdown_content" in result
        assert result["status_code"] == 200
        assert result["url"] == "https://example.com"
        assert result["content_length"] == len(result["markdown_content"])


def test_fetch_url_converts_html_to_markdown():
    with patch("agent.tools.fetch_url.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = "<h1>Title</h1><p>Paragraph</p>"
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fetch_url("https://example.com")
        # markdownify should produce markdown with "Title" and "Paragraph"
        assert "Title" in result["markdown_content"]
        assert "Paragraph" in result["markdown_content"]


def test_fetch_url_passes_timeout():
    with patch("agent.tools.fetch_url.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = "<p>ok</p>"
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        fetch_url("https://example.com", timeout=10)
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 10


def test_fetch_url_sets_user_agent():
    with patch("agent.tools.fetch_url.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = "<p>ok</p>"
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        fetch_url("https://example.com")
        _, kwargs = mock_get.call_args
        assert "User-Agent" in kwargs["headers"]


def test_fetch_url_http_error():
    with patch("agent.tools.fetch_url.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
        mock_get.return_value = mock_resp

        result = fetch_url("https://example.com/missing")
        assert "error" in result
        assert "404" in result["error"]
        assert result["url"] == "https://example.com/missing"


def test_fetch_url_connection_error():
    with patch("agent.tools.fetch_url.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")

        result = fetch_url("https://down.example.com")
        assert "error" in result


def test_fetch_url_timeout_error():
    with patch("agent.tools.fetch_url.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.Timeout("timed out")

        result = fetch_url("https://slow.example.com", timeout=1)
        assert "error" in result
