"""The browser window a thread's agent opened, as the dashboard shows and hands it over.

The dashboard embeds LangSmith's own page for the window, from the act link
LangSmith mints for it, and switches who controls the window through LangSmith.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.sandboxes.browsers import (
    BrowserGoneError,
    BrowserScope,
    BrowserTab,
    BrowserUnavailableError,
    BrowserWindow,
    ThreadBrowserWindow,
)
from openswe.threads.handlers import get_promptable_thread_metadata
from openswe.threads.summary import thread_is_owner
from openswe.ui_invalidations.topics import Topic

router = APIRouter(tags=["threads"])


class ThreadBrowser(BaseModel):
    """A thread's browser window as the dashboard shows it."""

    scope: BrowserScope
    controller: Literal["agent", "user"]
    reason: str
    tabs: list[BrowserTab]
    # Opens the window to whoever holds it until it expires, so it goes only to
    # people who may prompt the thread.
    act_url: str

    @classmethod
    def of(cls, record: ThreadBrowserWindow, window: BrowserWindow) -> ThreadBrowser:
        return cls(
            scope=record.scope,
            controller=window.controller,
            reason=window.reason,
            tabs=window.tabs,
            act_url=window.act_url,
        )


class BrowserControllerUpdate(BaseModel):
    controller: Literal["agent", "user"]


async def _thread_window(thread_id: str, login: str) -> ThreadBrowserWindow:
    metadata = await get_promptable_thread_metadata(thread_id, login)
    record = ThreadBrowserWindow.of(metadata)
    if record is None:
        raise HTTPException(404, "this thread has not opened a browser")
    # The owner's own browser holds their logins, so only they reach it, and
    # only while the thread is still private.
    if record.scope == "user" and (
        metadata.get("visibility") != "private" or not thread_is_owner(metadata, login)
    ):
        raise HTTPException(403, "only the thread owner can use their own browser")
    return record


@contextmanager
def _langsmith_errors() -> Iterator[None]:
    try:
        yield
    except BrowserGoneError as exc:
        raise HTTPException(410, "the thread's browser window is closed") from exc
    except BrowserUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/threads/{thread_id}/browser")
async def api_thread_browser(
    thread_id: str, response: Response, session: dict[str, str] = SESSION_DEP
) -> ThreadBrowser:
    record = await _thread_window(thread_id, session["sub"])
    with _langsmith_errors():
        window = await record.langsmith.window(record.window)
    response.headers["Cache-Control"] = "no-store"
    return ThreadBrowser.of(record, window)


@router.post("/threads/{thread_id}/browser/controller")
@audit_endpoint
async def api_thread_browser_controller(
    thread_id: str,
    body: BrowserControllerUpdate,
    response: Response,
    session: dict[str, str] = SESSION_DEP,
) -> ThreadBrowser:
    """Take the window over from the agent, or hand it back."""
    record = await _thread_window(thread_id, session["sub"])
    with _langsmith_errors():
        window = await record.langsmith.set_controller(record.window, body.controller)
    await Topic.THREAD_BROWSERS.invalidate(key=thread_id)
    response.headers["Cache-Control"] = "no-store"
    return ThreadBrowser.of(record, window)
