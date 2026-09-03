from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi.testclient import TestClient

from agent.api import oauth, routes


def test_terminal_ticket_is_short_lived_and_thread_bound(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-32-bytes")
    ticket = oauth.issue_terminal_ticket(
        login="alice", email="alice@example.com", thread_id="thread-1"
    )

    assert oauth.decode_terminal_ticket(ticket, thread_id="thread-1") == {
        "sub": "alice",
        "email": "alice@example.com",
    }
    with pytest.raises(HTTPException) as exc_info:
        oauth.decode_terminal_ticket(ticket, thread_id="thread-2")
    assert exc_info.value.status_code == 401


def test_terminal_ticket_rejects_expired_tokens(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-32-bytes")
    monkeypatch.setattr(oauth.time, "time", lambda: 0)
    ticket = oauth.issue_terminal_ticket(login="alice", email=None, thread_id="thread-1")

    with pytest.raises(HTTPException) as exc_info:
        oauth.decode_terminal_ticket(ticket, thread_id="thread-1")
    assert exc_info.value.status_code == 401


def test_cloud_terminal_url_uses_direct_langgraph_origin(monkeypatch) -> None:
    monkeypatch.setenv("LANGGRAPH_URL", "https://agent.example/base")

    assert routes._cloud_terminal_websocket_url("thread/1") == (
        "wss://agent.example/base/dashboard/api/threads/thread%2F1/terminal"
    )


async def test_terminal_connection_requires_owner_before_issuing_ticket(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("LANGGRAPH_URL", "https://agent.example")
    get_sandbox = AsyncMock(return_value=("sandbox-1", "repo"))
    monkeypatch.setattr(routes, "get_dashboard_terminal_sandbox", get_sandbox)
    response = Response()

    connection = await routes.api_thread_terminal_connection(
        "thread-1",
        response,
        {"sub": "alice", "email": "alice@example.com"},
    )

    get_sandbox.assert_awaited_once_with("thread-1", "alice", email="alice@example.com")
    assert response.headers["cache-control"] == "no-store"
    assert connection["url"] == ("wss://agent.example/dashboard/api/threads/thread-1/terminal")
    assert connection["protocol"] == "open-swe-terminal"
    assert connection["ticket"] not in connection["url"]
    assert (
        oauth.decode_terminal_ticket(connection["ticket"], thread_id="thread-1")["sub"] == "alice"
    )


def test_cloud_terminal_session_reads_ticket_from_subprotocol(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-32-bytes")
    ticket = oauth.issue_terminal_ticket(login="alice", email=None, thread_id="thread-1")
    websocket = cast(
        WebSocket,
        SimpleNamespace(
            headers={
                "sec-websocket-protocol": f"open-swe-terminal, {ticket}",
            }
        ),
    )

    assert routes._cloud_terminal_session(websocket, "thread-1") == {
        "sub": "alice",
        "email": None,
    }

    invalid_websocket = cast(
        WebSocket,
        SimpleNamespace(headers={"sec-websocket-protocol": ticket}),
    )
    with pytest.raises(HTTPException) as exc_info:
        routes._cloud_terminal_session(invalid_websocket, "thread-1")
    assert exc_info.value.status_code == 401


def test_cloud_terminal_route_accepts_ticket_without_dashboard_cookie(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-32-bytes")
    ticket = oauth.issue_terminal_ticket(login="alice", email=None, thread_id="thread-1")

    async def fake_cloud_terminal(
        websocket: WebSocket, thread_id: str, session: dict[str, object]
    ) -> None:
        assert thread_id == "thread-1"
        assert session["sub"] == "alice"
        await websocket.accept(subprotocol="open-swe-terminal")
        await websocket.send_json({"type": "ready"})
        await websocket.close()

    monkeypatch.setattr(routes, "_cloud_terminal", fake_cloud_terminal)
    app = FastAPI()
    app.include_router(routes.router)

    with TestClient(app).websocket_connect(
        "/dashboard/api/threads/thread-1/terminal",
        subprotocols=["open-swe-terminal", ticket],
    ) as websocket:
        assert websocket.accepted_subprotocol == "open-swe-terminal"
        assert websocket.receive_json() == {"type": "ready"}
