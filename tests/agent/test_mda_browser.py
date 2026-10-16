import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt import browser
from rt.browser import BrowserMiddleware, browser_enabled, browser_name, hydrate, redact


def _runtime(thread: str = "thread-1") -> SimpleNamespace:
    return SimpleNamespace(config={"configurable": {"thread_id": thread}})


def _session(page: Any) -> Any:
    session = browser._Session(browser=MagicMock(), stagehand=MagicMock())
    session.browser.context.pages = AsyncMock(return_value=[page])
    session.stagehand.close = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def _fresh_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser, "_SESSIONS", {})
    monkeypatch.setattr(browser, "_LOCKS", {})


def _tool(middleware: BrowserMiddleware, name: str) -> Any:
    return next(tool for tool in middleware.tools if tool.name == name)


def test_enabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_SWE_BROWSER", raising=False)
    assert not browser_enabled()
    monkeypatch.setenv("OPEN_SWE_BROWSER", "1")
    assert browser_enabled()


def test_browser_name_is_per_deployment_and_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "a")
    first = browser_name("thread-1")
    assert first == browser_name("thread-1")
    assert first != browser_name("thread-2")
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "b")
    assert first != browser_name("thread-1")


def test_redact_hides_keys() -> None:
    assert redact("wss://x/devtools?api_key=lsv2_secret&tenant_id=t") == (
        "wss://x/devtools?api_key=[redacted]&tenant_id=t"
    )


def test_hydrate_maps_snapshot_ids_to_xpaths() -> None:
    ids = {"1-2": "/html/body/button/text()[1]"}
    assert hydrate(ids, [{"op": "click", "id": "1-2"}]) == [
        {"op": "click", "selector": "xpath=/html/body/button", "value": None}
    ]
    assert "stale" in str(hydrate(ids, [{"op": "click", "id": "9-9"}]))
    assert "requires" in str(hydrate(ids, [{"op": "fill", "id": "1-2"}]))


async def test_run_reopens_a_dead_session_once() -> None:
    page = MagicMock()
    dead, live = _session(page), _session(page)
    dead.stagehand.experimental_batch = AsyncMock(side_effect=RuntimeError("websocket closed"))
    live.stagehand.experimental_batch = AsyncMock(return_value={"title": "Example"})
    opener = AsyncMock(side_effect=[dead, live])
    middleware = BrowserMiddleware(opener=opener)

    result = await _tool(middleware, "run").coroutine(
        runtime=_runtime(), code="return await page.title()"
    )

    assert result == '{"title":"Example"}'
    assert opener.await_count == 2
    dead.stagehand.close.assert_awaited_once()


async def test_errors_go_back_to_the_model_redacted() -> None:
    session = _session(MagicMock())
    session.stagehand.experimental_batch = AsyncMock(
        side_effect=ValueError("bad page at wss://h?api_key=lsv2_secret")
    )
    middleware = BrowserMiddleware(opener=AsyncMock(return_value=session))

    result = await _tool(middleware, "run").coroutine(runtime=_runtime(), code="throw 1")

    assert result == "error: ValueError: bad page at wss://h?api_key=[redacted]"


async def test_run_needs_exactly_one_of_code_or_actions() -> None:
    middleware = BrowserMiddleware(opener=AsyncMock())
    result = await _tool(middleware, "run").coroutine(runtime=_runtime())
    assert result.startswith("error: provide exactly one")


async def test_screenshot_returns_an_image_block() -> None:
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"jpg")
    middleware = BrowserMiddleware(opener=AsyncMock(return_value=_session(page)))

    result = await _tool(middleware, "screenshot").coroutine(
        runtime=_runtime(), type="jpeg", quality=40
    )

    assert result[1] == {"type": "image", "url": "data:image/jpeg;base64,anBn"}


async def test_after_agent_releases_the_client() -> None:
    session = _session(MagicMock())
    session.stagehand.experimental_batch = AsyncMock(return_value=1)
    middleware = BrowserMiddleware(opener=AsyncMock(return_value=session))
    await _tool(middleware, "run").coroutine(runtime=_runtime(), code="return 1")

    await middleware.aafter_agent({"messages": []}, _runtime())

    session.stagehand.close.assert_awaited_once()
    assert browser._SESSIONS == {}
