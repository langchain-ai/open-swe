from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi.testclient import TestClient

from agent.dashboard import oauth
from agent.threads import browser


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-32-bytes")


def test_browser_and_terminal_tickets_are_not_interchangeable() -> None:
    browser_ticket = oauth.issue_terminal_ticket(
        login="alice", email=None, thread_id="thread-1", audience=oauth.BROWSER_TICKET_AUDIENCE
    )
    terminal_ticket = oauth.issue_terminal_ticket(login="alice", email=None, thread_id="thread-1")

    assert oauth.decode_terminal_ticket(
        browser_ticket, thread_id="thread-1", audience=oauth.BROWSER_TICKET_AUDIENCE
    ) == {"sub": "alice", "email": None}
    with pytest.raises(HTTPException) as exc_info:
        oauth.decode_terminal_ticket(browser_ticket, thread_id="thread-1")
    assert exc_info.value.status_code == 401
    with pytest.raises(HTTPException) as exc_info:
        oauth.decode_terminal_ticket(
            terminal_ticket, thread_id="thread-1", audience=oauth.BROWSER_TICKET_AUDIENCE
        )
    assert exc_info.value.detail == "invalid browser ticket"


def test_cloud_browser_url_uses_direct_langgraph_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_URL", "https://agent.example/base")

    assert browser._cloud_browser_websocket_url("thread/1") == (
        "wss://agent.example/base/dashboard/api/threads/thread%2F1/browser"
    )


async def test_browser_connection_requires_owner_before_issuing_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_URL", "https://agent.example")
    get_sandbox = AsyncMock(return_value=("sandbox-1", "repo"))
    monkeypatch.setattr(browser, "get_dashboard_terminal_sandbox", get_sandbox)
    response = Response()

    connection = await browser.api_thread_browser_connection(
        "thread-1", response, {"sub": "alice", "email": "alice@example.com"}
    )

    get_sandbox.assert_awaited_once_with("thread-1", "alice", email="alice@example.com")
    assert response.headers["cache-control"] == "no-store"
    assert connection["url"] == "wss://agent.example/dashboard/api/threads/thread-1/browser"
    assert connection["protocol"] == "open-swe-browser"
    decoded = oauth.decode_terminal_ticket(
        connection["ticket"], thread_id="thread-1", audience=oauth.BROWSER_TICKET_AUDIENCE
    )
    assert decoded["sub"] == "alice"


def test_cloud_browser_session_reads_ticket_from_subprotocol() -> None:
    ticket = oauth.issue_terminal_ticket(
        login="alice", email=None, thread_id="thread-1", audience=oauth.BROWSER_TICKET_AUDIENCE
    )
    websocket = cast(
        WebSocket,
        SimpleNamespace(headers={"sec-websocket-protocol": f"open-swe-browser, {ticket}"}),
    )
    assert browser._cloud_browser_session(websocket, "thread-1") == {"sub": "alice", "email": None}

    terminal_ticket = oauth.issue_terminal_ticket(login="alice", email=None, thread_id="thread-1")
    wrong_audience = cast(
        WebSocket,
        SimpleNamespace(headers={"sec-websocket-protocol": f"open-swe-browser, {terminal_ticket}"}),
    )
    with pytest.raises(HTTPException) as exc_info:
        browser._cloud_browser_session(wrong_audience, "thread-1")
    assert exc_info.value.status_code == 401


def test_cloud_browser_route_accepts_ticket_without_dashboard_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = oauth.issue_terminal_ticket(
        login="alice", email=None, thread_id="thread-1", audience=oauth.BROWSER_TICKET_AUDIENCE
    )

    async def fake_cloud_browser(
        websocket: WebSocket, thread_id: str, session: dict[str, object]
    ) -> None:
        assert thread_id == "thread-1"
        assert session["sub"] == "alice"
        await websocket.accept(subprotocol="open-swe-browser")
        await websocket.send_json({"type": "ready"})
        await websocket.close()

    monkeypatch.setattr(browser, "_cloud_browser", fake_cloud_browser)
    app = FastAPI()
    app.include_router(browser.router, prefix="/dashboard/api")

    with TestClient(app).websocket_connect(
        "/dashboard/api/threads/thread-1/browser",
        subprotocols=["open-swe-browser", ticket],
    ) as websocket:
        assert websocket.accepted_subprotocol == "open-swe-browser"
        assert websocket.receive_json() == {"type": "ready"}


def test_rewrite_devtools_url_only_dials_the_tunnel() -> None:
    assert (
        browser.rewrite_devtools_url("ws://127.0.0.1:9222/devtools/browser/abc-123", 54321)
        == "ws://127.0.0.1:54321/devtools/browser/abc-123"
    )
    # Whatever host or query Chromium reports, only the path survives.
    assert browser.rewrite_devtools_url("ws://evil.example/devtools/page/1?x=1#f", 7) == (
        "ws://127.0.0.1:7/devtools/page/1"
    )
    with pytest.raises(ValueError):
        browser.rewrite_devtools_url("wss://127.0.0.1:9222/devtools/browser/x", 1)
    with pytest.raises(ValueError):
        browser.rewrite_devtools_url("ws://127.0.0.1:9222/not-devtools", 1)


def test_classify_client_frame_separates_control_from_devtools_traffic() -> None:
    assert browser.classify_client_frame('{"type":"install"}') == ("control", {"type": "install"})
    assert browser.classify_client_frame('{"id":1,"method":"Target.getTargets"}') == (
        "cdp",
        {"id": 1, "method": "Target.getTargets"},
    )
    assert browser.classify_client_frame('{"id":"1","method":"x"}') == ("drop", None)
    assert browser.classify_client_frame("[1,2]") == ("drop", None)
    assert browser.classify_client_frame("not json") == ("drop", None)
    assert browser.classify_client_frame("x" * (browser._MAX_CLIENT_FRAME_BYTES + 1)) == (
        "drop",
        None,
    )


def test_parse_browser_probe_reads_the_last_status_line() -> None:
    probe = browser.parse_browser_probe(
        'noise from a login shell\n{"status":"missing"}\n{"status":"ready","browser":"/usr/bin/chromium"}\n'
    )
    assert probe.status == "ready"
    assert probe.browser == "/usr/bin/chromium"
    assert browser.parse_browser_probe('{"status":"missing"}').status == "missing"
    with pytest.raises(ValueError):
        browser.parse_browser_probe("nothing useful\n{not json}")
