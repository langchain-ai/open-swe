"""The LangSmith browser MDA gave a thread's agent, as the dashboard embeds it.

The dashboard frames LangSmith's own page for the window the agent drives, from
an act link minted for the dashboard's origin. The page carries its own Take
control and Hand back buttons and tells the dashboard who controls the window.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from openswe.dashboard.deps import SESSION_DEP
from openswe.sandboxes.browsers import (
    AGENT_WINDOW,
    BrowserGoneError,
    BrowserTab,
    BrowserUnavailableError,
    ThreadBrowser,
)
from openswe.threads.handlers import get_promptable_thread_metadata
from openswe.utils.dashboard_links import dashboard_base_url

router = APIRouter(tags=["threads"])


class ThreadBrowserView(BaseModel):
    """A thread's browser window as the dashboard embeds it."""

    tabs: list[BrowserTab]
    # Opens the window to whoever holds it until it expires, so it goes only to
    # people who may prompt the thread, and only the dashboard may frame it.
    act_url: str
    expires_at: datetime


def _dashboard_origin() -> str:
    parts = urlsplit(dashboard_base_url())
    if not parts.scheme or not parts.netloc:
        raise HTTPException(503, "no dashboard URL is configured to embed the browser in")
    return f"{parts.scheme}://{parts.netloc}"


@contextmanager
def _langsmith_errors() -> Iterator[None]:
    try:
        yield
    except BrowserGoneError as exc:
        raise HTTPException(410, "the thread's browser is closed") from exc
    except BrowserUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/threads/{thread_id}/browser")
async def api_thread_browser(
    thread_id: str, response: Response, session: dict[str, str] = SESSION_DEP
) -> ThreadBrowserView:
    record = ThreadBrowser.of(await get_promptable_thread_metadata(thread_id, session["sub"]))
    if record is None:
        raise HTTPException(404, "this thread's agent has not used a browser")
    with _langsmith_errors():
        window = await record.langsmith.window_named(AGENT_WINDOW)
        links = await record.langsmith.links(window.id, embed_origins=[_dashboard_origin()])
    response.headers["Cache-Control"] = "no-store"
    return ThreadBrowserView(tabs=window.tabs, act_url=links.act_url, expires_at=links.expires_at)
