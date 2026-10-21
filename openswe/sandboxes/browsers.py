"""The LangSmith browser MDA gives a thread's agent, as the dashboard shows it.

MDA's browser middleware names and opens each thread's browser and drives one
window of it, ``mda``. The ``mda/`` project reports the browser's name through
the backend's ``browser`` hook, and the dashboard frames that window from an
act link LangSmith mints for the dashboard's origin.
"""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Self

import httpx2
from pydantic import BaseModel, ValidationError

from openswe.sandboxes.providers.langsmith_api import SandboxAPI
from openswe.ui_invalidations.topics import Topic
from openswe.utils.thread_ops import langgraph_client

# Creating and starting wait for Chrome to answer.
_BROWSER_START_TIMEOUT_SECONDS = 180.0
_BROWSER_REQUEST_TIMEOUT_SECONDS = 30.0
_GONE_STATUSES = frozenset({"failed", "deleting"})
_THREAD_METADATA_KEY = "browser"
# The window MDA's browser middleware drives.
AGENT_WINDOW = "mda"

logger = logging.getLogger(__name__)


class BrowserUnavailableError(RuntimeError):
    """LangSmith refused the browser beta for this deployment."""


class BrowserGoneError(RuntimeError):
    """The browser or its window no longer exists in LangSmith, or can no longer run."""


class BrowserTab(BaseModel):
    id: str
    title: str = ""
    url: str = ""


class BrowserWindow(BaseModel):
    """One window of a browser."""

    id: str
    name: str = ""
    tabs: list[BrowserTab] = []


class WindowLinks(BaseModel):
    """A window's view and act links, minted for pages on the given origins to frame."""

    view_url: str
    act_url: str
    expires_at: datetime


class BrowserState(BaseModel):
    id: str
    name: str
    status: str
    # Listed only while the browser is running.
    attachments: list[BrowserWindow] = []


class LangSmithBrowser:
    """One browser in LangSmith's browser beta, by sandbox name."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def window_named(self, name: str) -> BrowserWindow:
        """The window called ``name``, starting the browser when it is stopped."""
        state = await self._running_state()
        for window in state.attachments:
            if window.name == name:
                return window
        raise BrowserGoneError(f"browser {self.name} has no window {name!r}")

    async def links(self, window_id: str, *, embed_origins: list[str]) -> WindowLinks:
        """Mint view and act links that pages on ``embed_origins`` may show in an iframe."""
        response = await _browser_request(
            "POST",
            f"/browsers/{self.name}/attachments/{window_id}/links",
            json={"embed_origins": embed_origins},
            timeout=_BROWSER_REQUEST_TIMEOUT_SECONDS,
        )
        return WindowLinks.model_validate(response.json())

    async def _running_state(self) -> BrowserState:
        state = await self._state()
        if state.status == "stopped":
            # Starting returns once Chrome answers, with every window it had.
            await _browser_request(
                "POST", f"/boxes/{self.name}/start", timeout=_BROWSER_START_TIMEOUT_SECONDS
            )
            state = await self._state()
        if state.status in _GONE_STATUSES:
            raise BrowserGoneError(f"browser {self.name} is {state.status}")
        return state

    async def _state(self) -> BrowserState:
        """The browser as LangSmith reports it; only its own 404 means it is gone."""
        try:
            response = await _browser_request(
                "GET", f"/browsers/{self.name}", timeout=_BROWSER_REQUEST_TIMEOUT_SECONDS
            )
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise BrowserGoneError(f"browser {self.name} no longer exists") from exc
            raise
        return BrowserState.model_validate(response.json())


class ThreadBrowser(BaseModel):
    """The browser MDA gave a thread's agent, kept in the thread's metadata."""

    browser: str

    @classmethod
    def of(cls, metadata: Mapping[str, object]) -> Self | None:
        raw = metadata.get(_THREAD_METADATA_KEY)
        if raw is None:
            return None
        try:
            return cls.model_validate(raw)
        except ValidationError:
            logger.warning("Ignoring a malformed thread browser", exc_info=True)
            return None

    @property
    def langsmith(self) -> LangSmithBrowser:
        return LangSmithBrowser(self.browser)

    async def save(self, thread_id: str) -> None:
        await langgraph_client().threads.update(
            thread_id=thread_id, metadata={_THREAD_METADATA_KEY: self.model_dump()}
        )
        await Topic.THREAD_BROWSERS.invalidate(key=thread_id)


async def _browser_request(
    method: Literal["GET", "POST"],
    path: str,
    *,
    json: dict[str, object] | None = None,
    timeout: float,
) -> httpx2.Response:
    try:
        return await SandboxAPI.from_env().request(method, path, json=json, timeout=timeout)
    except httpx2.HTTPStatusError as exc:
        if exc.response.status_code == 501:
            raise BrowserUnavailableError(
                "LangSmith's browser beta is not enabled for this deployment's workspace"
            ) from exc
        raise
