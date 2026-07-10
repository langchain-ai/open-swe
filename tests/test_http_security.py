from __future__ import annotations

import importlib
import json
import socket as real_socket
import sys
import types
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

exa_py_stub = types.ModuleType("exa_py")
exa_py_stub.Exa = object
sys.modules.setdefault("exa_py", exa_py_stub)

importlib.import_module("agent.tools.fetch_url")
importlib.import_module("agent.tools.http_request")
fetch_url_tool = sys.modules["agent.tools.fetch_url"]
http_request_tool = sys.modules["agent.tools.http_request"]
# SSRF resolution now lives in the shared validator; patch DNS there.
url_safety = importlib.import_module("agent.utils.url_safety")

_NO_JSON = object()


def _addr_info(ip: str, port: int | None = None) -> tuple:
    return (
        real_socket.AF_INET,
        real_socket.SOCK_STREAM,
        6,
        "",
        (ip, port or 0),
    )


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        headers: dict[str, str] | None = None,
        text: str = "",
        json_data: object = _NO_JSON,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self.text = text
        self._json_data = json_data

    def json(self) -> object:
        if self._json_data is _NO_JSON:
            raise ValueError("response is not json")
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code} error", request=None, response=None)


class FakeAsyncClient:
    """Records each request and replays programmed responses.

    ``responder(method, url, **kwargs)`` returns a ``FakeResponse``. The class is
    installed in place of ``httpx.AsyncClient`` on the tool module under test.
    """

    last_instance: FakeAsyncClient | None = None

    def __init__(self, responder, *args: Any, **kwargs: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []
        FakeAsyncClient.last_instance = self

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responder(method, url, **kwargs)


def _install_client(monkeypatch, module, responder) -> type:
    def factory(*args: Any, **kwargs: Any) -> FakeAsyncClient:
        return FakeAsyncClient(responder, *args, **kwargs)

    fake_httpx = types.SimpleNamespace(
        AsyncClient=factory,
        HTTPError=httpx.HTTPError,
        TimeoutException=httpx.TimeoutException,
    )
    monkeypatch.setattr(module, "httpx", fake_httpx)
    return factory


# --- _resolve_and_validate (pure IP gating) ----------------------------------


def test_resolve_and_validate_rejects_unsupported_scheme() -> None:
    is_safe, reason, _, _ = http_request_tool._resolve_and_validate("ftp://example.com/x")
    assert is_safe is False
    assert "scheme" in reason.lower()


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "169.254.169.254", "10.0.0.5", "192.168.1.1"],
)
def test_resolve_and_validate_rejects_private_ranges(monkeypatch, ip: str) -> None:
    monkeypatch.setattr(
        url_safety.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [_addr_info(ip, port)],
    )
    is_safe, reason, hostname, _ = http_request_tool._resolve_and_validate("http://evil.test/")
    assert is_safe is False
    assert "blocked address" in reason
    assert hostname == "evil.test"


def test_resolve_and_validate_accepts_public_ip(monkeypatch) -> None:
    monkeypatch.setattr(
        url_safety.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [_addr_info("93.184.216.34", port)],
    )
    is_safe, reason, hostname, addr_infos = http_request_tool._resolve_and_validate(
        "https://example.com/path"
    )
    assert is_safe is True
    assert reason == ""
    assert hostname == "example.com"
    assert addr_infos[0][4][0] == "93.184.216.34"


def test_pinned_url_rewrites_host_to_ip_keeping_path_and_port() -> None:
    assert (
        http_request_tool._pinned_url("https://example.com:8443/a/b?q=1", "93.184.216.34")
        == "https://93.184.216.34:8443/a/b?q=1"
    )
    # IPv6 literal is bracketed
    assert http_request_tool._pinned_url("http://h/x", "::1").startswith("http://[::1]/x")


# --- fetch_url ---------------------------------------------------------------


async def test_fetch_url_blocks_private_ip_without_issuing_a_request(monkeypatch) -> None:
    def fail_responder(*args: Any, **kwargs: Any) -> FakeResponse:
        raise AssertionError("request should not be issued for blocked URLs")

    _install_client(monkeypatch, fetch_url_tool, fail_responder)
    # Real DNS resolution of the metadata IP literal yields the private IP itself.

    result = await fetch_url_tool.fetch_url(
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
    )

    assert result["status_code"] == 0
    assert "Request blocked" in result["error"]
    assert result["url"].startswith("http://169.254.169.254/")


async def test_fetch_url_blocks_redirects_to_private_ips(monkeypatch) -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        ip = "93.184.216.34" if host == "example.com" else host
        return [_addr_info(ip, port)]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)

    def responder(method: str, url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(
            status_code=302,
            url=url,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
        )

    _install_client(monkeypatch, fetch_url_tool, responder)

    result = await fetch_url_tool.fetch_url("https://example.com/start")

    # First hop targets the validated public IP, with Host preserved.
    client = FakeAsyncClient.last_instance
    assert client is not None
    assert len(client.calls) == 1
    first = client.calls[0]
    assert urlparse(first["url"]).hostname == "93.184.216.34"
    assert first["headers"]["Host"] == "example.com"
    assert first["extensions"]["sni_hostname"] == "example.com"
    # The redirect to a private IP was blocked before a second request was issued.
    assert result["status_code"] == 0
    assert result["url"] == "http://169.254.169.254/latest/meta-data"
    assert "Request blocked" in result["error"]


# --- http_request ------------------------------------------------------------


async def test_http_request_pins_connection_to_validated_public_ip(monkeypatch) -> None:
    """Validation sees a public IP and the connection must target that exact IP.

    A resolver that later flips to a private address cannot rebind because the
    request URL is pinned to the validated IP (with Host + SNI preserved).
    """
    hostname = "rebind.example.com"
    public_addr = "93.184.216.34"
    private_addr = "127.0.0.1"

    call_count = {"n": 0}

    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        ip = public_addr if call_count["n"] == 1 else private_addr
        return [_addr_info(ip, port)]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)

    def responder(method: str, url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(status_code=200, url=url, text="ok", json_data="ok")

    _install_client(monkeypatch, http_request_tool, responder)

    result = await http_request_tool.http_request(f"http://{hostname}/probe")

    client = FakeAsyncClient.last_instance
    assert client is not None
    assert len(client.calls) == 1
    call = client.calls[0]
    assert urlparse(call["url"]).hostname == public_addr, (
        f"connection must target pinned public IP, got {call['url']}"
    )
    assert call["headers"]["Host"] == hostname
    assert call["extensions"]["sni_hostname"] == hostname
    assert result["status_code"] == 200


async def test_http_request_blocks_when_only_private_ips(monkeypatch) -> None:
    """If the first resolution returns a private IP, no request is issued."""
    hostname = "evil.example.com"
    private_addr = "169.254.169.254"

    monkeypatch.setattr(
        url_safety.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [_addr_info(private_addr, port)],
    )

    def fail_responder(*args: Any, **kwargs: Any) -> FakeResponse:
        raise AssertionError("request should not be issued for blocked URLs")

    _install_client(monkeypatch, http_request_tool, fail_responder)

    result = await http_request_tool.http_request(f"http://{hostname}/")

    assert result["status_code"] == 0
    assert "Request blocked" in result["content"]


async def test_http_request_downgrades_method_on_303(monkeypatch) -> None:
    """A 303 redirect must switch the follow-up request to GET and drop the body."""

    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [_addr_info("93.184.216.34", port)]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)

    def responder(method: str, url: str, **kwargs: Any) -> FakeResponse:
        if "start" in url:
            return FakeResponse(
                status_code=303,
                url=url,
                headers={"Location": "https://example.com/done"},
            )
        return FakeResponse(status_code=200, url=url, json_data={"ok": True})

    _install_client(monkeypatch, http_request_tool, responder)

    result = await http_request_tool.http_request(
        "https://example.com/start", method="POST", data={"x": 1}
    )

    client = FakeAsyncClient.last_instance
    assert client is not None
    assert len(client.calls) == 2
    assert client.calls[0]["method"] == "POST"
    assert client.calls[1]["method"] == "GET"
    assert "json" not in client.calls[1] and "content" not in client.calls[1]
    assert result["status_code"] == 200
    assert result["content"] == {"ok": True}


async def test_http_request_returns_timeout_result(monkeypatch) -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [_addr_info("93.184.216.34", port)]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)

    def responder(method: str, url: str, **kwargs: Any) -> FakeResponse:
        raise httpx.TimeoutException("timed out")

    _install_client(monkeypatch, http_request_tool, responder)

    result = await http_request_tool.http_request("https://example.com/", timeout=7)

    assert result["success"] is False
    assert result["status_code"] == 0
    assert "timed out after 7 seconds" in result["content"]


async def test_http_request_offloads_oversized_response(monkeypatch) -> None:
    monkeypatch.setattr(
        url_safety.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [_addr_info("93.184.216.34", port)],
    )
    large_content = "sensitive marker " + "x" * 1_000

    def responder(method: str, url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(
            status_code=200,
            url=url,
            headers={"content-type": "application/json"},
            json_data={"payload": large_content},
        )

    _install_client(monkeypatch, http_request_tool, responder)
    monkeypatch.setattr(http_request_tool, "HTTP_REQUEST_MAX_INLINE_CHARS", 100)
    writes: list[tuple[str, str, str]] = []

    async def fake_write(tool_name: str, content: str, extension: str) -> str:
        writes.append((tool_name, content, extension))
        return "/workspace/http-response-result.jsonl"

    monkeypatch.setattr(http_request_tool, "write_sandbox_output", fake_write)

    result = await http_request_tool.http_request("https://example.com/data")

    saved_response = "".join(json.loads(line)["text"] for line in writes[0][1].splitlines())
    assert result["success"] is True
    assert result["response_path"] == "/workspace/http-response-result.jsonl"
    assert result["response_chars"] == len(saved_response)
    assert "content" not in result
    assert "headers" not in result
    assert large_content not in str(result)
    assert writes[0][0::2] == ("http-response", "jsonl")
    assert large_content in saved_response


async def test_http_request_does_not_inline_oversized_response_when_write_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(http_request_tool, "HTTP_REQUEST_MAX_INLINE_CHARS", 100)
    large_content = "sensitive marker " + "x" * 1_000

    async def fail_write(tool_name: str, content: str, extension: str) -> str:
        raise RuntimeError("sandbox unavailable")

    monkeypatch.setattr(http_request_tool, "write_sandbox_output", fail_write)

    result = await http_request_tool._offload_large_response(
        {
            "success": True,
            "status_code": 200,
            "headers": {},
            "content": large_content,
            "url": "https://example.com/data",
        }
    )

    assert result["success"] is False
    assert "could not be saved" in result["content"]
    assert large_content not in str(result)
