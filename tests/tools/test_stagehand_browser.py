import pytest

from agent.integrations import stagehand_browser


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
