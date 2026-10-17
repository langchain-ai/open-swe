import importlib

import pytest

from openswe.sandboxes.browsers import BrowserTab, BrowserWindow

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
    return owners, browser


@pytest.mark.parametrize(
    ("visibility", "scope", "expected"),
    [
        ("public", None, "workspace:acme"),
        ("private", None, "person:alice"),
        ("private", "workspace", "workspace:acme"),
    ],
)
async def test_open_browser_picks_the_threads_browser(
    monkeypatch: pytest.MonkeyPatch, visibility: str, scope: str | None, expected: str
) -> None:
    owners, browser = _configure(monkeypatch, visibility=visibility, owner_login="alice")

    result = await browser_tool.open_browser("http://localhost:5173/login?next=/", scope=scope)

    assert owners == [expected]
    assert result["localhost"] == "http://sb-abc.localhost"
    assert browser.opened == ["http://sb-abc.localhost:5173/login?next=/"]


async def test_workspace_thread_cannot_open_a_private_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners, _ = _configure(monkeypatch, visibility="public", owner_login="alice")

    result = await browser_tool.open_browser(scope="private")

    assert result.get("ok") is False
    assert owners == []


async def test_private_thread_opens_only_its_owners_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners, _ = _configure(monkeypatch, visibility="private", owner_login="bob")

    with pytest.raises(ValueError, match="Only the owner of a private thread"):
        await browser_tool.open_browser()

    assert owners == []
