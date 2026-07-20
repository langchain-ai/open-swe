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
        self.handler = None

    async def route(self, pattern: str, handler: object) -> None:
        self.pattern = pattern
        self.handler = handler

    async def request(self, url: str) -> FakeRoute:
        assert self.handler is not None
        route = FakeRoute(url)
        await self.handler(route, route.request)
        return route


class FakeSession:
    id = "session-123"

    def __init__(self) -> None:
        self.page = FakePage()
        self.ended = False

    async def navigate(self, url: str) -> None:
        self.page.url = url

    async def act(self, input: str) -> dict[str, object]:
        self.page.url = input
        return {"result": "ok"}

    async def end(self) -> None:
        self.ended = True


@pytest.mark.asyncio
async def test_browser_url_guard_aborts_internal_browser_requests() -> None:
    session = FakeSession()
    await stagehand_browser._install_browser_url_guard(session)

    route = await session.page.request("http://169.254.169.254/latest/meta-data/")

    assert route.aborted is True
    assert route.continued is False
    assert stagehand_browser._blocked_request_error(session) is not None


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
