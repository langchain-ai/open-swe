import httpx2
import pytest

from openswe.sandboxes import browsers
from openswe.sandboxes.browsers import BrowserGoneError, BrowserOwner, LangSmithBrowser

_READY = {"id": "1", "name": "browser-1", "status": "ready", "attachments": []}


def _not_found() -> httpx2.HTTPStatusError:
    request = httpx2.Request("GET", "https://api.example/v2/sandboxes/browsers/browser-1")
    return httpx2.HTTPStatusError(
        "not found", request=request, response=httpx2.Response(404, request=request)
    )


def _track_browsers(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Record each ``BrowserOwner.browser`` call's ``replacing`` argument."""
    calls: list[str | None] = []

    async def browser(_owner: BrowserOwner, *, replacing: str | None = None) -> LangSmithBrowser:
        calls.append(replacing)
        return LangSmithBrowser("browser-1")

    monkeypatch.setattr(BrowserOwner, "browser", browser)
    return calls


async def test_a_404_on_a_window_route_keeps_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _track_browsers(monkeypatch)

    async def request(method: str, path: str, **_: object) -> httpx2.Response:
        if method == "GET":
            return httpx2.Response(200, json=_READY)
        raise _not_found()

    monkeypatch.setattr(browsers, "_browser_request", request)

    with pytest.raises(httpx2.HTTPStatusError):
        await BrowserOwner().window_for("sandbox-1")

    assert calls == [None]


async def test_the_browsers_own_404_replaces_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _track_browsers(monkeypatch)

    async def request(method: str, path: str, **_: object) -> httpx2.Response:
        raise _not_found()

    monkeypatch.setattr(browsers, "_browser_request", request)

    with pytest.raises(BrowserGoneError):
        await BrowserOwner().window_for("sandbox-1")

    assert calls == [None, "browser-1"]
