from collections.abc import Mapping
from typing import NotRequired, TypedDict

from openswe.audit_logs.tools import audit_tool
from openswe.credential_scope import private_owner_login
from openswe.run_config import RunConfig
from openswe.sandboxes.browsers import BrowserOwner, BrowserScope, ThreadBrowserWindow
from openswe.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend
from openswe.tools.access import Policy, access
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_reads import read_thread


class OpenBrowserTab(TypedDict):
    id: str
    url: str


class OpenBrowserResult(TypedDict):
    scope: BrowserScope
    cdp_url: str
    controller: str
    localhost: str
    view_url: NotRequired[str]
    tab: NotRequired[OpenBrowserTab]


_WORKSPACE_BROWSER = Policy(trusted="anywhere")
# A person's browser holds their logins, so only their own private threads reach it.
_USER_BROWSER = Policy(trusted="private", actor="owner")


def _browser_policy(args: Mapping[str, object]) -> Policy:
    return _USER_BROWSER if args.get("scope") == "user" else _WORKSPACE_BROWSER


@audit_tool()
@access(_WORKSPACE_BROWSER, per_call=_browser_policy)
async def open_browser(scope: BrowserScope, url: str | None = None) -> OpenBrowserResult:
    """Implement the `open_browser` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")

    metadata = thread_metadata(await read_thread(thread_id))
    if scope == "user":
        login = private_owner_login(cfg, metadata)
        if login is None:
            raise ValueError(
                "Only the owner of a private thread can open their own browser; "
                'pass scope="workspace" for the workspace browser'
            )
        owner = await BrowserOwner.person(login)
    else:
        if not cfg.workspace_slug:
            raise ValueError("This thread has no workspace, so it has no workspace browser")
        owner = await BrowserOwner.workspace(cfg.workspace_slug, actor_login=cfg.github_login)

    backend = unwrap_sandbox_backend(await get_sandbox_backend(thread_id))
    browser, window = await owner.window_for(backend.id)
    opened = ThreadBrowserWindow(scope=scope, browser=browser.name, window=window.id)
    if ThreadBrowserWindow.of(metadata) != opened:
        await opened.save(thread_id)
    result: OpenBrowserResult = {
        "scope": scope,
        "cdp_url": window.cdp_url,
        "controller": window.controller,
        "localhost": f"http://{window.localhost_alias}",
    }
    if window.view_url:
        result["view_url"] = window.view_url
    if url:
        tab = await browser.open_tab(window, url)
        result["tab"] = {"id": tab.id, "url": tab.url or window.localhost_url(url)}
    return result
