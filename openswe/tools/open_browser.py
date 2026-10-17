from collections.abc import Mapping
from typing import NotRequired, TypedDict

from openswe.audit_logs.tools import audit_tool
from openswe.credential_scope import private_owner_login
from openswe.run_config import RunConfig
from openswe.sandboxes.browsers import BrowserOwner, BrowserScope
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
    localhost: NotRequired[str]
    view_url: NotRequired[str]
    tab: NotRequired[OpenBrowserTab]


def _private_browser(args: Mapping[str, object]) -> Policy | None:
    return Policy(trusted="private", actor="owner") if args.get("scope") == "private" else None


@audit_tool()
@access(Policy(trusted="anywhere"), per_call=_private_browser)
async def open_browser(
    url: str | None = None, scope: BrowserScope | None = None
) -> OpenBrowserResult:
    """Implement the `open_browser` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")

    metadata = thread_metadata(await read_thread(thread_id))
    private_thread = metadata.get("visibility", "public") != "public"
    scope = scope or ("private" if private_thread else "workspace")
    if scope == "private":
        login = private_owner_login(cfg, metadata)
        if login is None:
            raise ValueError(
                "Only the owner of a private thread can open their private browser; "
                'pass scope="workspace" for the workspace browser'
            )
        owner = await BrowserOwner.person(login)
    else:
        if not cfg.workspace_slug:
            raise ValueError("This thread has no workspace, so it has no workspace browser")
        owner = await BrowserOwner.workspace(cfg.workspace_slug, actor_login=cfg.github_login)

    backend = unwrap_sandbox_backend(await get_sandbox_backend(thread_id))
    browser, window = await owner.window_for(backend.id)
    result: OpenBrowserResult = {
        "scope": scope,
        "cdp_url": window.cdp_url,
        "controller": window.controller,
    }
    if window.localhost_alias:
        result["localhost"] = f"http://{window.localhost_alias}"
    if window.view_url:
        result["view_url"] = window.view_url
    if url:
        tab = await browser.open_tab(window, url)
        result["tab"] = {"id": tab.id, "url": tab.url or window.localhost_url(url)}
    return result
