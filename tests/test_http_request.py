from __future__ import annotations

from unittest.mock import patch

import requests

from agent.tools.http_request import _blocked_response, _is_url_safe, http_request


# -- _is_url_safe ---------------------------------------------------------


def test_is_url_safe_allows_public_ip():
    """A URL that resolves to a public IP should be allowed."""
    with patch("agent.tools.http_request.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        safe, reason = _is_url_safe("https://example.com")
        assert safe is True
        assert reason == ""


def test_is_url_safe_blocks_private_ip():
    with patch("agent.tools.http_request.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("10.0.0.1", 0))]
        safe, reason = _is_url_safe("http://internal.corp")
        assert safe is False
        assert "blocked" in reason.lower()


def test_is_url_safe_blocks_loopback():
    with patch("agent.tools.http_request.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("127.0.0.1", 0))]
        safe, reason = _is_url_safe("http://localhost")
        assert safe is False
        assert "blocked" in reason.lower()


def test_is_url_safe_blocks_link_local():
    with patch("agent.tools.http_request.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("169.254.169.254", 0))]
        safe, reason = _is_url_safe("http://169.254.169.254/latest/meta-data/")
        assert safe is False
        assert "blocked" in reason.lower()


def test_is_url_safe_blocks_reserved_ipv6():
    with patch("agent.tools.http_request.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(10, 1, 6, "", ("::1", 0, 0, 0))]
        safe, reason = _is_url_safe("http://[::1]/")
        assert safe is False


def test_is_url_safe_rejects_unparseable_hostname():
    safe, reason = _is_url_safe("not-a-url")
    assert safe is False


def test_is_url_safe_rejects_unresolvable_hostname():
    with patch("agent.tools.http_request.socket.getaddrinfo") as mock_gai:
        import socket

        mock_gai.side_effect = socket.gaierror("Name or service not known")
        safe, reason = _is_url_safe("http://does-not-exist.invalid")
        assert safe is False
        assert "resolve" in reason.lower()


# -- _blocked_response ----------------------------------------------------


def test_blocked_response_shape():
    resp = _blocked_response("http://evil.com", "blocked IP")
    assert resp["success"] is False
    assert resp["status_code"] == 0
    assert "blocked IP" in resp["content"]
    assert resp["url"] == "http://evil.com"


# -- http_request ---------------------------------------------------------


def test_http_request_blocked_by_ssrf_check():
    """Requests to private IPs should be blocked before any network call."""
    with patch("agent.tools.http_request.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("10.0.0.1", 0))]
        result = http_request("http://10.0.0.1/admin")
        assert result["success"] is False
        assert "blocked" in result["content"].lower()


def test_http_request_get_success():
    with patch("agent.tools.http_request._is_url_safe", return_value=(True, "")):
        with patch("agent.tools.http_request.requests.request") as mock_req:
            mock_resp = mock_req.return_value
            mock_resp.status_code = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.json.return_value = {"ok": True}
            mock_resp.url = "https://api.example.com/data"

            result = http_request("https://api.example.com/data")
            assert result["success"] is True
            assert result["status_code"] == 200
            assert result["content"] == {"ok": True}


def test_http_request_post_with_json_body():
    with patch("agent.tools.http_request._is_url_safe", return_value=(True, "")):
        with patch("agent.tools.http_request.requests.request") as mock_req:
            mock_resp = mock_req.return_value
            mock_resp.status_code = 201
            mock_resp.headers = {}
            mock_resp.json.return_value = {"id": 1}
            mock_resp.url = "https://api.example.com/items"

            result = http_request(
                "https://api.example.com/items",
                method="POST",
                data={"name": "item1"},
            )
            assert result["success"] is True
            assert result["status_code"] == 201
            # Verify json kwarg was used for dict data
            _, kwargs = mock_req.call_args
            assert kwargs["json"] == {"name": "item1"}


def test_http_request_post_with_string_body():
    with patch("agent.tools.http_request._is_url_safe", return_value=(True, "")):
        with patch("agent.tools.http_request.requests.request") as mock_req:
            mock_resp = mock_req.return_value
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_resp.json.side_effect = ValueError
            mock_resp.text = "OK"
            mock_resp.url = "https://api.example.com"

            result = http_request("https://api.example.com", method="POST", data="raw-body")
            assert result["content"] == "OK"
            _, kwargs = mock_req.call_args
            assert kwargs["data"] == "raw-body"


def test_http_request_timeout():
    with patch("agent.tools.http_request._is_url_safe", return_value=(True, "")):
        with patch("agent.tools.http_request.requests.request") as mock_req:
            mock_req.side_effect = requests.exceptions.Timeout("timed out")
            result = http_request("https://slow.example.com", timeout=5)
            assert result["success"] is False
            assert "timed out" in result["content"].lower()


def test_http_request_connection_error():
    with patch("agent.tools.http_request._is_url_safe", return_value=(True, "")):
        with patch("agent.tools.http_request.requests.request") as mock_req:
            mock_req.side_effect = requests.exceptions.ConnectionError("refused")
            result = http_request("https://down.example.com")
            assert result["success"] is False
            assert "error" in result["content"].lower()


def test_http_request_4xx_not_success():
    with patch("agent.tools.http_request._is_url_safe", return_value=(True, "")):
        with patch("agent.tools.http_request.requests.request") as mock_req:
            mock_resp = mock_req.return_value
            mock_resp.status_code = 404
            mock_resp.headers = {}
            mock_resp.json.side_effect = ValueError
            mock_resp.text = "Not Found"
            mock_resp.url = "https://api.example.com/missing"

            result = http_request("https://api.example.com/missing")
            assert result["success"] is False
            assert result["status_code"] == 404


def test_http_request_passes_headers_and_params():
    with patch("agent.tools.http_request._is_url_safe", return_value=(True, "")):
        with patch("agent.tools.http_request.requests.request") as mock_req:
            mock_resp = mock_req.return_value
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_resp.json.return_value = {}
            mock_resp.url = "https://api.example.com"

            http_request(
                "https://api.example.com",
                headers={"Authorization": "Bearer tok"},
                params={"page": "1"},
            )
            _, kwargs = mock_req.call_args
            assert kwargs["headers"] == {"Authorization": "Bearer tok"}
            assert kwargs["params"] == {"page": "1"}
