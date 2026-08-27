"""The socket route's own auth, since it deliberately opts out of the CSRF check."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

DEVICE = "8740518e36a8f61d28ef8781abadd125"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "a" * 32)
    monkeypatch.setenv("LANGGRAPH_URL", "http://127.0.0.1:2024")
    # An allowlist makes the origin check active rather than a no-op, which is
    # the condition the desktop actually hit.
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://open-swe.example.com")
    from agent.local_runner.routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _ticket(device_id: str = DEVICE) -> str:
    from agent.dashboard.oauth import issue_runner_ticket

    return issue_runner_ticket(login="alice", email=None, device_id=device_id)


def test_a_client_with_no_origin_can_open_a_socket(client: TestClient) -> None:
    """Electron's main process sends no Origin header.

    The socket route carries no ambient credential for an origin allowlist to
    defend — it takes a signed ticket — so requiring one would reject the only
    caller it has.
    """
    from agent.local_runner.broker import runner_broker

    with client.websocket_connect(
        f"/dashboard/api/desktop/runner/socket/{DEVICE}",
        subprotocols=["open-swe-runner", _ticket()],
    ):
        assert runner_broker.connection("alice", DEVICE) is not None
    assert runner_broker.connection("alice", DEVICE) is None


def test_a_ticket_for_another_device_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/dashboard/api/desktop/runner/socket/{DEVICE}",
            subprotocols=["open-swe-runner", _ticket("ffffffffffffffffffffffffffffffff")],
        ):
            pass


def test_an_unsigned_ticket_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/dashboard/api/desktop/runner/socket/{DEVICE}",
            subprotocols=["open-swe-runner", "not-a-ticket"],
        ):
            pass


def test_a_socket_offered_without_a_ticket_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/dashboard/api/desktop/runner/socket/{DEVICE}",
            subprotocols=["open-swe-runner"],
        ):
            pass


def test_issuing_a_ticket_still_requires_a_trusted_origin(client: TestClient) -> None:
    """The CSRF check moves to `/connect` rather than disappearing.

    That POST rides the session cookie, so it is the one an attacker's page
    could otherwise forge — and it is the only way to obtain a ticket.
    """
    response = client.post(
        "/dashboard/api/desktop/runner/connect",
        json={"device_id": DEVICE},
        headers={"origin": "https://evil.example.com"},
        cookies={"osw_session": "whatever"},
    )
    assert response.status_code == 403


def test_a_device_id_that_is_not_a_plain_name_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/dashboard/api/desktop/runner/socket/../../etc",
            subprotocols=["open-swe-runner", _ticket()],
        ):
            pass
