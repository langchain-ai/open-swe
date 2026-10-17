import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openswe.dashboard import oauth
from openswe.threads import browser


def test_terminal_ticket_cannot_open_the_browser_view(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-32-bytes")
    ticket = oauth.issue_terminal_ticket(login="alice", email=None, thread_id="thread-1")
    app = FastAPI()
    app.include_router(browser.router, prefix="/dashboard/api")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with TestClient(app).websocket_connect(
            "/dashboard/api/threads/thread-1/browser?tab=tab-1",
            subprotocols=["open-swe-browser", ticket],
        ):
            pass
    assert exc_info.value.code == 1008


class _Viewer:
    def __init__(self, messages: list[str]) -> None:
        self._messages = messages
        self.sent: list[str] = []

    async def receive_text(self) -> str:
        if not self._messages:
            raise WebSocketDisconnect()
        return self._messages.pop(0)

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


class _Upstream:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def __aiter__(self) -> _Upstream:
        return self

    async def __anext__(self) -> str:
        await asyncio.Event().wait()
        raise StopAsyncIteration

    async def send(self, message: str) -> None:
        self.sent.append(message)


async def test_relay_forwards_only_live_view_input() -> None:
    click = json.dumps({"type": "mouse", "event": "mousePressed", "x": 1, "y": 2})
    viewer = _Viewer([json.dumps({"type": "resize", "width": 1}), "not json", click])
    upstream = _Upstream()

    with pytest.raises(WebSocketDisconnect):
        await browser._relay(viewer, upstream)  # type: ignore[arg-type]

    assert upstream.sent == [click]
