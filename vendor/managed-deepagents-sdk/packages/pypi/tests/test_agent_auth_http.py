from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import URLError

import httpx
import pytest

import managed_deepagents._agent_auth_http as auth_http
from managed_deepagents._agent_auth import AgentAuth, AgentAuthContext
from managed_deepagents._agent_auth_http import request_agent_auth_json


@dataclass
class RecordedRequest:
    address: tuple[str, int]
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class AuthServer:
    url: str = ""
    requests: list[RecordedRequest] = field(default_factory=list)
    status: int = 200
    body: bytes = b'{"ok": true}'
    headers: dict[str, str] = field(default_factory=dict)


@pytest.fixture
def auth_server() -> Iterator[AuthServer]:
    state = AuthServer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            state.requests.append(
                RecordedRequest(
                    self.client_address,
                    self.path,
                    {key.lower(): value for key, value in self.headers.items()},
                    self.rfile.read(int(self.headers.get("Content-Length", "0"))),
                )
            )
            self.send_response(state.status)
            self.send_header("Content-Length", str(len(state.body)))
            for key, value in state.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(state.body)

        def do_POST(self) -> None:
            self.do_GET()

        def log_message(self, message: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        state.url = f"http://127.0.0.1:{server.server_port}"
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield state
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_reuses_connection_across_agent_auth_instances(auth_server: AuthServer) -> None:
    for principal in ("first", "second"):
        auth = AgentAuth(
            AgentAuthContext(
                base_url=auth_server.url,
                agent_id="agent",
                principal_id=principal,
                api_key=principal,
                workspace_id=principal,
            )
        )
        assert auth._urlopen_json("GET", "/v1/agent-auth/oauth-providers") == (
            200,
            {"ok": True},
        )

    first, second = auth_server.requests
    assert first.address == second.address
    assert first.headers["x-api-key"] == "first"
    assert second.headers["x-api-key"] == "second"
    assert second.headers["x-tenant-id"] == "second"


def test_concurrent_requests_keep_caller_headers(auth_server: AuthServer) -> None:
    def request(caller: int) -> tuple[int, dict[str, object]]:
        return request_agent_auth_json(
            "GET",
            f"{auth_server.url}/{caller}",
            headers={
                "x-api-key": str(caller),
                "x-tenant-id": str(caller),
                "x-langsmith-identity": str(caller),
            },
            timeout=2,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(request, range(8))) == [(200, {"ok": True})] * 8

    assert len(auth_server.requests) == 8
    for recorded in auth_server.requests:
        caller = recorded.path.removeprefix("/")
        assert recorded.headers["x-api-key"] == caller
        assert recorded.headers["x-tenant-id"] == caller
        assert recorded.headers["x-langsmith-identity"] == caller


def test_response_cookies_and_caller_headers_are_not_shared(auth_server: AuthServer) -> None:
    auth_server.headers = {"Set-Cookie": "session=first; Path=/"}
    request_agent_auth_json(
        "GET",
        auth_server.url,
        headers={"x-api-key": "first", "x-langsmith-identity": "first"},
        timeout=2,
    )
    request_agent_auth_json("GET", auth_server.url, headers={}, timeout=2)

    first, second = auth_server.requests
    assert first.address == second.address
    assert not auth_http._CLIENT.cookies
    assert "cookie" not in second.headers
    assert "x-api-key" not in second.headers
    assert "x-langsmith-identity" not in second.headers


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_are_returned_without_following(auth_server: AuthServer, status: int) -> None:
    auth_server.status = status
    auth_server.headers = {"Location": f"{auth_server.url}/target"}
    assert request_agent_auth_json("GET", auth_server.url, headers={}, timeout=2) == (
        status,
        {"ok": True},
    )
    assert len(auth_server.requests) == 1


@pytest.mark.parametrize(
    ("status", "raw", "expected"),
    [
        (200, b'{"ok": true}', {"ok": True}),
        (400, b'{"detail": "Bad request"}', {"detail": "Bad request"}),
        (503, b'{"detail": "Unavailable"}', {"detail": "Unavailable"}),
        (204, b"", {}),
        (200, b"not json", {}),
        (200, b"\xff", {}),
        (200, b"[]", {}),
        (200, b"null", {}),
    ],
)
def test_status_and_json_decoding(
    auth_server: AuthServer, status: int, raw: bytes, expected: dict[str, object]
) -> None:
    auth_server.status = status
    auth_server.body = raw
    assert request_agent_auth_json("GET", auth_server.url, headers={}, timeout=2) == (
        status,
        expected,
    )


def test_request_body_is_json(auth_server: AuthServer) -> None:
    request_agent_auth_json(
        "POST",
        auth_server.url,
        headers={"content-type": "application/json"},
        body={"name": "example"},
        timeout=2,
    )
    assert auth_server.requests[0].body == b'{"name": "example"}'


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectTimeout("connect timed out"),
        httpx.ReadTimeout("read timed out"),
        httpx.WriteTimeout("write timed out"),
        httpx.PoolTimeout("pool timed out"),
        httpx.ConnectError("connection failed"),
    ],
)
def test_transport_failures_remain_os_errors(
    monkeypatch: pytest.MonkeyPatch, failure: httpx.RequestError
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise failure

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(auth_http, "_CLIENT", client)
        with pytest.raises(URLError) as raised:
            request_agent_auth_json("GET", "https://example.com", headers={}, timeout=0.25)

    assert isinstance(raised.value, OSError)
    assert raised.value.reason is failure
    assert len(requests) == 1
    assert requests[0].extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 0.25
    )
