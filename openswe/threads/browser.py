"""The LangSmith browser MDA gave a thread's agent, as the dashboard embeds it.

The dashboard frames LangSmith's own page for the window the agent drives, from
an act link minted for the dashboard's origin. A person can open that window
before the agent does; the agent then adopts it. The page carries its own Take
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
    BrowserWindow,
    ThreadBrowser,
)
from openswe.threads.handlers import get_promptable_thread_metadata
from openswe.ui_invalidations.topics import Topic
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


async def _record(thread_id: str, login: str) -> ThreadBrowser:
    record = ThreadBrowser.of(await get_promptable_thread_metadata(thread_id, login))
    if record is None:
        raise HTTPException(404, "this thread's agent hasn't started, so it has no browser yet")
    return record


async def _view(
    record: ThreadBrowser, window: BrowserWindow, response: Response
) -> ThreadBrowserView:
    links = await record.langsmith.links(window.id, embed_origins=[_dashboard_origin()])
    response.headers["Cache-Control"] = "no-store"
    return ThreadBrowserView(tabs=window.tabs, act_url=links.act_url, expires_at=links.expires_at)


@router.get("/threads/{thread_id}/browser")
async def api_thread_browser(
    thread_id: str, response: Response, session: dict[str, str] = SESSION_DEP
) -> ThreadBrowserView:
    record = await _record(thread_id, session["sub"])
    if not record.opened:
        raise HTTPException(404, "this thread's browser isn't open")
    with _langsmith_errors():
        return await _view(record, await record.langsmith.window_named(AGENT_WINDOW), response)


@router.post("/threads/{thread_id}/browser")
async def api_open_thread_browser(
    thread_id: str, response: Response, session: dict[str, str] = SESSION_DEP
) -> ThreadBrowserView:
    """Open the thread's browser for a person, before or after the agent has."""
    record = await _record(thread_id, session["sub"])
    with _langsmith_errors():
        window = await record.open(thread_id)
        view = await _view(record, window, response)
    await Topic.THREAD_BROWSERS.invalidate(key=thread_id)
    return view
