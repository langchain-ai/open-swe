import importlib

import pytest

from openswe.sandboxes.browsers import BrowserTab, BrowserWindow, ThreadBrowserWindow

browser_tool = importlib.import_module("openswe.tools.open_browser")
access_module = importlib.import_module("openswe.tools.access")

_WINDOW = BrowserWindow(
    id="att-1",
    sandbox="sandbox-1",
    cdp_url="http://browser-1.sandbox.internal:9300",
    localhost_alias="sb-abc.localhost",
    controller="agent",
)


class _Backend:
    id = "sandbox-1"


class _Browser:
    name = "browser-1"

    def __init__(self) -> None:
        self.opened: list[str] = []

    async def open_tab(self, window: BrowserWindow, url: str) -> BrowserTab:
        self.opened.append(window.localhost_url(url))
        return BrowserTab(id="tab-1", url=window.localhost_url(url))


class _Owner:
    def __init__(self, scope: str, browser: _Browser) -> None:
        self.scope = scope
        self.browser = browser

    async def window_for(self, sandbox: str) -> tuple[_Browser, BrowserWindow]:
        assert sandbox == "sandbox-1"
        return self.browser, _WINDOW


def _configure(
    monkeypatch: pytest.MonkeyPatch, *, visibility: str, owner_login: str
) -> tuple[list[str], _Browser]:
    metadata = {"visibility": visibility, "owner_type": "user", "owner_login": owner_login}
    monkeypatch.setattr(
        "openswe.run_config.get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-1",
                "github_login": "alice",
                "workspace": "acme",
            }
        },
    )

    async def read_thread(_thread_id: str) -> dict[str, object]:
        return {"metadata": metadata}

    async def not_admin(*_args: object, **_kwargs: object) -> bool:
        return False

    async def get_backend(_thread_id: str) -> _Backend:
        return _Backend()

    owners: list[str] = []
    browser = _Browser()

    async def person(login: str) -> _Owner:
        owners.append(f"person:{login}")
        return _Owner("private", browser)

    async def workspace(slug: str, *, actor_login: str | None) -> _Owner:
        owners.append(f"workspace:{slug}")
        return _Owner("workspace", browser)

    monkeypatch.setattr(access_module, "read_thread", read_thread)
    monkeypatch.setattr(access_module, "actor_is_admin", not_admin)
    monkeypatch.setattr(browser_tool, "read_thread", read_thread)
    monkeypatch.setattr(browser_tool, "get_sandbox_backend", get_backend)
    monkeypatch.setattr(browser_tool, "unwrap_sandbox_backend", lambda value: value)
    monkeypatch.setattr(browser_tool.BrowserOwner, "person", person)
    monkeypatch.setattr(browser_tool.BrowserOwner, "workspace", workspace)

    async def save(window: ThreadBrowserWindow, thread_id: str) -> None:
        owners.append(f"saved:{window.scope}")

    monkeypatch.setattr(browser_tool.ThreadBrowserWindow, "save", save)
    return owners, browser


@pytest.mark.parametrize(
    ("visibility", "scope", "owner"),
    [
        ("public", "workspace", "workspace:acme"),
        ("private", "user", "person:alice"),
        ("private", "workspace", "workspace:acme"),
    ],
)
async def test_open_browser_opens_the_requested_browser(
    monkeypatch: pytest.MonkeyPatch, visibility: str, scope: str, owner: str
) -> None:
    owners, browser = _configure(monkeypatch, visibility=visibility, owner_login="alice")

    result = await browser_tool.open_browser(scope, url="http://localhost:5173/login?next=/")

    assert owners == [owner, f"saved:{scope}"]
    assert result["localhost"] == "http://sb-abc.localhost"
    assert browser.opened == ["http://sb-abc.localhost:5173/login?next=/"]


@pytest.mark.parametrize(
    ("visibility", "owner_login"),
    [("public", "alice"), ("private", "bob")],
)
async def test_only_the_owners_private_thread_opens_their_browser(
    monkeypatch: pytest.MonkeyPatch, visibility: str, owner_login: str
) -> None:
    owners, _ = _configure(monkeypatch, visibility=visibility, owner_login=owner_login)

    result = await browser_tool.open_browser("user")

    assert result.get("ok") is False
    assert owners == []
