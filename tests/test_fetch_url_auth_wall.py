from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import httpx

exa_py_stub = types.ModuleType("exa_py")
exa_py_stub.Exa = object
sys.modules.setdefault("exa_py", exa_py_stub)

importlib.import_module("agent.tools.fetch_url")
fetch_url_tool = sys.modules["agent.tools.fetch_url"]
url_safety = importlib.import_module("agent.utils.url_safety")

import socket as real_socket  # noqa: E402


def _addr_info(ip: str, port: int | None = None) -> tuple:
    return (real_socket.AF_INET, real_socket.SOCK_STREAM, 6, "", (ip, port or 0))


class FakeResponse:
    def __init__(self, *, status_code: int, url: str, text: str = "") -> None:
        self.status_code = status_code
        self.url = url
        self.headers: dict[str, str] = {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code} error", request=None, response=None)


class FakeAsyncClient:
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


def _install_client(monkeypatch, responder) -> None:
    def factory(*args: Any, **kwargs: Any) -> FakeAsyncClient:
        return FakeAsyncClient(responder, *args, **kwargs)

    fake_httpx = types.SimpleNamespace(
        AsyncClient=factory,
        HTTPError=httpx.HTTPError,
        TimeoutException=httpx.TimeoutException,
    )
    monkeypatch.setattr(fetch_url_tool, "httpx", fake_httpx)


def _stub_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        url_safety.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [_addr_info("93.184.216.34", port)],
    )


NOTION_SIGNIN_HTML = """
<!doctype html>
<html>
  <head><title>Log in — Notion</title></head>
  <body>
    <div>Sign in to see this page</div>
    <a href="https://www.notion.so/login">Log in</a>
  </body>
</html>
"""


REAL_CONTENT_HTML = """
<!doctype html>
<html>
  <head><title>Product Spec — v1</title></head>
  <body>
    <h1>Product Spec</h1>
    <p>This is a long document describing the product requirements in detail.</p>
    <p>Section 1: Goals. We want to build a fast and reliable system for our users
    that handles many different edge cases including retries, timeouts, and partial
    failures. This section contains plenty of prose to exceed the short-body
    heuristic used by the auth-wall detector.</p>
    <p>Section 2: Non-goals. We are not attempting to solve real-time streaming
    in this version. That is deferred to a future milestone.</p>
    <p>Section 3: API. The primary entrypoint is a REST endpoint that accepts a
    JSON payload and returns a JSON response. Errors are surfaced as standard
    HTTP status codes with structured error bodies.</p>
  </body>
</html>
""" + ("<p>filler paragraph to ensure the body is long enough.</p>" * 40)


async def test_fetch_url_flags_notion_signin_as_inaccessible(monkeypatch) -> None:
    _stub_dns(monkeypatch)

    def responder(method: str, url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(status_code=200, url=url, text=NOTION_SIGNIN_HTML)

    _install_client(monkeypatch, responder)

    result = await fetch_url_tool.fetch_url("https://www.notion.so/some-private-doc")

    assert result["status_code"] == 200
    assert result["accessible"] is False
    assert result["auth_wall_reason"] == "notion_signin"


async def test_fetch_url_marks_real_content_as_accessible(monkeypatch) -> None:
    _stub_dns(monkeypatch)

    def responder(method: str, url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(status_code=200, url=url, text=REAL_CONTENT_HTML)

    _install_client(monkeypatch, responder)

    result = await fetch_url_tool.fetch_url("https://example.com/spec")

    assert result["status_code"] == 200
    assert result["accessible"] is True
    assert result["auth_wall_reason"] is None
