import asyncio
import json
from collections.abc import Awaitable, Callable

import pytest

from agent.integrations import stagehand_browser


@pytest.fixture(autouse=True)
def clear_stagehand_sessions() -> None:
    stagehand_browser._SESSIONS.clear()


@pytest.mark.asyncio
async def test_browser_navigate_blocks_internal_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_get_session(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("browser session should not start for blocked URLs")

    monkeypatch.setattr(stagehand_browser, "_get_session", fail_get_session)

    result = await stagehand_browser.browser_navigate("http://127.0.0.1:8000")

    assert result["success"] is False
    assert "blocked" in result["error"]


def test_session_meta_does_not_return_cdp_url(monkeypatch: pytest.MonkeyPatch) -> None:
    class Data:
        cdp_url = "wss://connect.browserbase.com/?signingKey=secret"

    class Session:
        id = "session-123"
        data = Data()

    monkeypatch.setattr(stagehand_browser, "_is_local", lambda: False)

    assert stagehand_browser._session_meta(Session()) == {
        "session_id": "session-123",
        "replay_url": "https://www.browserbase.com/sessions/session-123",
    }


def test_browserbase_project_id_is_forwarded_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stagehand_browser, "_is_local", lambda: False)
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "project-123")

    assert stagehand_browser._browserbase_session_create_params() == {"project_id": "project-123"}


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = FakeRequest(url)
        self.aborted = False
        self.continued = False

    async def abort(self) -> None:
        self.aborted = True

    async def continue_(self) -> None:
        self.continued = True


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.handler: Callable[[FakeRoute, FakeRequest], Awaitable[None]] | None = None

    async def route(
        self, pattern: str, handler: Callable[[FakeRoute, FakeRequest], Awaitable[None]]
    ) -> None:
        self.pattern = pattern
        self.handler = handler

    async def request(self, url: str) -> FakeRoute:
        assert self.handler is not None
        route = FakeRoute(url)
        await self.handler(route, route.request)
        return route


class FakeData:
    cdp_url: str | None = None


class FakeSession:
    id = "session-123"
    _open_swe_cdp_guard: stagehand_browser._CDPBrowserURLGuard

    def __init__(self) -> None:
        self.data = FakeData()
        self.page = FakePage()
        self.ended = False

    async def navigate(self, url: str) -> None:
        self.page.url = url

    async def act(self, input: str) -> dict[str, object]:
        self.page.url = input
        return {"result": "ok"}

    async def observe(self, instruction: str) -> dict[str, object]:
        return {"result": instruction}

    async def extract(
        self, instruction: str, schema: dict[str, object] | None = None
    ) -> dict[str, object]:
        return {"result": {"instruction": instruction, "schema": schema}}

    async def end(self) -> None:
        self.ended = True


class FakeCDPWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.responses: dict[str, dict[str, object]] = {}
        self.response_sequences: dict[str, list[dict[str, object]]] = {}
        self.method_errors: dict[str, dict[str, object]] = {}
        self.target_errors: dict[str, dict[str, object]] = {}
        self.target_responses: dict[str, dict[str, object]] = {}
        self.held_methods: set[str] = set()
        self.closed = False

    def __aiter__(self) -> "FakeCDPWebSocket":
        return self

    async def __anext__(self) -> str:
        message = await self.incoming.get()
        if message is None:
            raise StopAsyncIteration
        return message

    async def send(self, message: str) -> None:
        parsed = json.loads(message)
        self.sent.append(parsed)
        method = parsed.get("method")
        if isinstance(method, str) and method in self.held_methods:
            return
        await self.respond(parsed)

    async def respond(self, parsed: dict[str, object]) -> None:
        method = parsed.get("method")
        message_id = parsed.get("id")
        if not isinstance(message_id, int):
            return
        if isinstance(method, str) and method in self.method_errors:
            await self.incoming.put(
                json.dumps({"id": message_id, "error": self.method_errors[method]})
            )
            return
        params = parsed.get("params")
        target_id = params.get("targetId") if isinstance(params, dict) else None
        if method == "Target.attachToTarget" and isinstance(target_id, str):
            error = self.target_errors.get(target_id)
            if error is not None:
                await self.incoming.put(json.dumps({"id": message_id, "error": error}))
                return
            result = self.target_responses.get(
                target_id, self.responses.get("Target.attachToTarget", {})
            )
        elif isinstance(method, str) and self.response_sequences.get(method):
            result = self.response_sequences[method].pop(0)
        else:
            result = self.responses.get(method, {}) if isinstance(method, str) else {}
        await self.incoming.put(json.dumps({"id": message_id, "result": result}))

    async def respond_to_method(self, method: str) -> None:
        for message in self.sent:
            if message.get("method") == method:
                await self.respond(message)
                return
        raise AssertionError(f"No CDP message sent for {method}")

    async def close(self) -> None:
        self.closed = True
        await self.incoming.put(None)

    async def emit(self, message: dict[str, object]) -> None:
        await self.incoming.put(json.dumps(message))


@pytest.mark.asyncio
async def test_browser_url_guard_aborts_internal_browser_requests() -> None:
    session = FakeSession()
    await stagehand_browser._install_browser_url_guard(session)

    route = await session.page.request("http://169.254.169.254/latest/meta-data/")

    assert route.aborted is True
    assert route.continued is False
    assert stagehand_browser._blocked_request_error(session) is not None


@pytest.mark.asyncio
async def test_cdp_browser_url_guard_blocks_real_stagehand_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    session.data.cdp_url = "ws://browser.example/devtools/browser/session-123"
    del session.page
    websocket = FakeCDPWebSocket()
    websocket.responses["Target.getTargets"] = {
        "targetInfos": [
            {
                "targetId": "initial-target",
                "type": "page",
                "url": "about:blank",
            }
        ]
    }
    websocket.target_responses["initial-target"] = {"sessionId": "cdp-session-initial"}

    async def connect_cdp_websocket(_cdp_url: str) -> FakeCDPWebSocket:
        return websocket

    monkeypatch.setattr(stagehand_browser, "_connect_cdp_websocket", connect_cdp_websocket)

    await stagehand_browser._install_browser_url_guard(session)
    await websocket.emit(
        {
            "method": "Target.attachedToTarget",
            "params": {
                "sessionId": "cdp-session-1",
                "targetInfo": {
                    "targetId": "new-target",
                    "type": "page",
                    "url": "https://example.com/",
                },
                "waitingForDebugger": True,
            },
        }
    )
    for _ in range(100):
        if "cdp-session-1" in session._open_swe_cdp_guard._attached_sessions:
            break
        await asyncio.sleep(0.01)
    await websocket.emit(
        {
            "method": "Fetch.requestPaused",
            "sessionId": "cdp-session-1",
            "params": {
                "requestId": "request-1",
                "request": {"url": "http://169.254.169.254/latest/meta-data/"},
            },
        }
    )
    await asyncio.sleep(0)

    assert stagehand_browser._blocked_request_error(session) is not None
    assert any(message["method"] == "Fetch.enable" for message in websocket.sent)
    assert any(message["method"] == "Fetch.failRequest" for message in websocket.sent)

    await session._open_swe_cdp_guard.close()


@pytest.mark.asyncio
async def test_cdp_browser_url_guard_waits_for_initial_fetch_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    session.data.cdp_url = "ws://browser.example/devtools/browser/session-123"
    del session.page
    websocket = FakeCDPWebSocket()
    websocket.responses["Target.getTargets"] = {
        "targetInfos": [
            {
                "targetId": "target-1",
                "type": "page",
                "url": "about:blank",
            }
        ]
    }
    websocket.responses["Target.attachToTarget"] = {"sessionId": "cdp-session-1"}
    websocket.held_methods.add("Fetch.enable")

    async def connect_cdp_websocket(_cdp_url: str) -> FakeCDPWebSocket:
        return websocket

    monkeypatch.setattr(stagehand_browser, "_connect_cdp_websocket", connect_cdp_websocket)

    install_task = asyncio.create_task(stagehand_browser._install_browser_url_guard(session))
    for _ in range(100):
        if any(message["method"] == "Fetch.enable" for message in websocket.sent):
            break
        await asyncio.sleep(0.01)

    assert install_task.done() is False
    assert any(message["method"] == "Fetch.enable" for message in websocket.sent)

    websocket.held_methods.remove("Fetch.enable")
    await websocket.respond_to_method("Fetch.enable")
    await install_task

    assert "cdp-session-1" in session._open_swe_cdp_guard._attached_sessions

    await session._open_swe_cdp_guard.close()


@pytest.mark.asyncio
async def test_cdp_browser_url_guard_ignores_stale_initial_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    session.data.cdp_url = "ws://browser.example/devtools/browser/session-123"
    del session.page
    websocket = FakeCDPWebSocket()
    stale_target = {"targetId": "stale-target", "type": "page", "url": "about:blank"}
    live_target = {"targetId": "live-target", "type": "page", "url": "about:blank"}
    websocket.response_sequences["Target.getTargets"] = [
        {"targetInfos": [stale_target, live_target]},
        {"targetInfos": [live_target]},
    ]
    websocket.target_errors["stale-target"] = {
        "code": -32602,
        "message": "No target with given id found",
    }
    websocket.target_responses["live-target"] = {"sessionId": "cdp-session-live"}

    async def connect_cdp_websocket(_cdp_url: str) -> FakeCDPWebSocket:
        return websocket

    monkeypatch.setattr(stagehand_browser, "_connect_cdp_websocket", connect_cdp_websocket)

    await stagehand_browser._install_browser_url_guard(session)

    guard = session._open_swe_cdp_guard
    assert guard._protected_target_ids == {"live-target"}
    assert "cdp-session-live" in guard._attached_sessions
    assert any(
        message["method"] == "Target.setAutoAttach"
        and isinstance((params := message.get("params")), dict)
        and params.get("waitForDebuggerOnStart") is True
        for message in websocket.sent
    )

    await guard.close()


@pytest.mark.asyncio
async def test_get_session_fails_closed_when_fetch_interception_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    session.data.cdp_url = "ws://browser.example/devtools/browser/session-123"
    del session.page
    websocket = FakeCDPWebSocket()
    live_target = {"targetId": "live-target", "type": "page", "url": "about:blank"}
    websocket.responses["Target.getTargets"] = {"targetInfos": [live_target]}
    websocket.target_responses["live-target"] = {"sessionId": "cdp-session-live"}
    websocket.method_errors["Fetch.enable"] = {
        "code": -32000,
        "message": "Fetch interception unavailable",
    }

    class FakeSessions:
        async def start(self, **_kwargs: object) -> FakeSession:
            return session

    class FakeClient:
        def __init__(self) -> None:
            self.sessions = FakeSessions()
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    client = FakeClient()

    async def connect_cdp_websocket(_cdp_url: str) -> FakeCDPWebSocket:
        return websocket

    monkeypatch.setattr(stagehand_browser, "_connect_cdp_websocket", connect_cdp_websocket)
    monkeypatch.setattr(stagehand_browser, "_build_client", lambda: client)

    with pytest.raises(stagehand_browser.BrowserNavigationBlocked):
        await stagehand_browser._get_session()

    assert session.ended is True
    assert client.closed is True
    assert websocket.closed is True
    assert stagehand_browser._SESSIONS == {}


@pytest.mark.asyncio
async def test_browser_url_guard_allows_public_browser_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stagehand_browser, "is_url_safe", lambda _url: (True, ""))
    session = FakeSession()
    await stagehand_browser._install_browser_url_guard(session)

    route = await session.page.request("https://example.com/")

    assert route.continued is True
    assert route.aborted is False
    assert stagehand_browser._blocked_request_error(session) is None


@pytest.mark.asyncio
async def test_browser_navigate_reports_blocked_redirect_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    await stagehand_browser._install_browser_url_guard(session)

    async def get_session(*_args: object, **_kwargs: object) -> FakeSession:
        return session

    async def navigate_with_redirect(url: str) -> None:
        session.page.url = url
        await session.page.request("http://169.254.169.254/latest/meta-data/")

    def fake_is_url_safe(url: str) -> tuple[bool, str]:
        if "169.254.169.254" in url:
            return False, "metadata endpoint"
        return True, ""

    monkeypatch.setattr(stagehand_browser, "_get_session", get_session)
    monkeypatch.setattr(stagehand_browser, "is_url_safe", fake_is_url_safe)
    monkeypatch.setattr(session, "navigate", navigate_with_redirect)
    stagehand_browser._SESSIONS["default"] = (object(), session)

    result = await stagehand_browser.browser_navigate("https://example.com/")

    assert result["success"] is False
    assert "169.254.169.254" in result["error"]
    assert session.ended is True


@pytest.mark.asyncio
async def test_browser_act_reports_unsafe_final_url(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()

    async def get_session(*_args: object, **_kwargs: object) -> FakeSession:
        return session

    monkeypatch.setattr(stagehand_browser, "_get_session", get_session)
    stagehand_browser._SESSIONS["default"] = (object(), session)

    result = await stagehand_browser.browser_act("http://169.254.169.254/latest/meta-data/")

    assert result["success"] is False
    assert "blocked after navigation" in result["error"]
    assert session.ended is True


@pytest.mark.asyncio
async def test_browser_act_fails_on_existing_blocked_background_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    stagehand_browser._mark_blocked_request(
        session, "http://169.254.169.254/latest/meta-data/", "metadata endpoint"
    )

    async def get_session(*_args: object, **_kwargs: object) -> FakeSession:
        return session

    monkeypatch.setattr(stagehand_browser, "_get_session", get_session)
    stagehand_browser._SESSIONS["default"] = (object(), session)

    result = await stagehand_browser.browser_act("click continue")

    assert result["success"] is False
    assert "blocked browser request" in result["error"]
    assert session.ended is True
