"""The LangSmith browser MDA gives a thread's agent, as the dashboard shows it.

MDA's browser middleware names each thread's browser and drives one window of
it, ``mda``. The ``mda/`` project reports the browser's name through the
backend's ``browser`` hook when a run starts, and again once the agent opens
it. A person can open it first: MDA adopts an existing browser and ``mda``
window. The dashboard frames that window from an act link LangSmith mints for
the dashboard's origin.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Self

import httpx2
from pydantic import BaseModel

from openswe.sandboxes.providers.langsmith_api import SandboxAPI
from openswe.ui_invalidations.topics import Topic
from openswe.utils.thread_ops import langgraph_client

# Creating and starting wait for Chrome to answer.
_BROWSER_START_TIMEOUT_SECONDS = 180.0
# MDA's browser middleware's default, so a browser a person opens idles out like the agent's.
_BROWSER_IDLE_TTL_SECONDS = 600
_BROWSER_REQUEST_TIMEOUT_SECONDS = 30.0
_GONE_STATUSES = frozenset({"failed", "deleting"})
_NAME_KEY = "browser_name"
_OPENED_KEY = "browser_opened"
# The record before names were reported early: {"browser": name}, always opened.
_LEGACY_KEY = "browser"
# The window MDA's browser middleware drives.
AGENT_WINDOW = "mda"


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
        window = self._find(await self._running_state(), name)
        if window is None:
            raise BrowserGoneError(f"browser {self.name} has no window {name!r}")
        return window

    async def open_window(self, name: str) -> BrowserWindow:
        """The window called ``name``, creating the browser and the window when missing.

        The window starts with the agent in control, as MDA opens it, so the agent
        can drive it at once; a person takes control from its page.
        """
        try:
            await _browser_request(
                "POST",
                "/browsers",
                json={"name": self.name, "idle_ttl_seconds": _BROWSER_IDLE_TTL_SECONDS},
                timeout=_BROWSER_START_TIMEOUT_SECONDS,
            )
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
        window = self._find(await self._running_state(), name)
        if window is not None:
            return window
        response = await _browser_request(
            "POST",
            f"/browsers/{self.name}/attachments",
            json={"name": name, "controller": "agent"},
            timeout=_BROWSER_START_TIMEOUT_SECONDS,
        )
        return BrowserWindow.model_validate(response.json())

    @staticmethod
    def _find(state: BrowserState, name: str) -> BrowserWindow | None:
        return next((window for window in state.attachments if window.name == name), None)

    async def links(self, window_id: str, *, embed_origins: list[str]) -> WindowLinks:
        """Mint view and act links that pages on ``embed_origins`` may show in an iframe."""
        response = await _browser_request(
            "POST",
            f"/browsers/{self.name}/attachments/{window_id}/links",
            json={"embed_origins": embed_origins},
            timeout=_BROWSER_REQUEST_TIMEOUT_SECONDS,
        )
        return WindowLinks.model_validate(response.json())

    async def delete(self) -> None:
        """Delete the browser and its profile; MDA opens a fresh one when the agent next browses."""
        try:
            await _browser_request(
                "DELETE", f"/boxes/{self.name}", timeout=_BROWSER_REQUEST_TIMEOUT_SECONDS
            )
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

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
    """The browser MDA names for a thread's agent, kept in the thread's metadata.

    The name and whether it is opened are separate keys, and each report writes
    only what it knows, so a run starting never undoes the agent having browsed.
    """

    browser: str
    # Whether the agent or a person has opened it.
    opened: bool

    @classmethod
    def of(cls, metadata: Mapping[str, object]) -> Self | None:
        name = metadata.get(_NAME_KEY)
        if isinstance(name, str) and name:
            return cls(browser=name, opened=metadata.get(_OPENED_KEY) is True)
        legacy = metadata.get(_LEGACY_KEY)
        if isinstance(legacy, Mapping) and isinstance(legacy.get("browser"), str):
            # Before MDA reported unopened browsers, only opened ones were recorded.
            return cls(browser=legacy["browser"], opened=metadata.get(_OPENED_KEY) is not False)
        return None

    @property
    def state(self) -> Literal["opened", "available"]:
        return "opened" if self.opened else "available"

    @property
    def langsmith(self) -> LangSmithBrowser:
        return LangSmithBrowser(self.browser)

    async def forget(self) -> dict[str, object]:
        """Delete the browser, signed-in sessions and all; the metadata update that records it.

        MDA keeps the name, and opens a fresh browser under it when the agent next browses.
        """
        await self.langsmith.delete()
        return {_NAME_KEY: self.browser, _OPENED_KEY: False}

    async def report(self, thread_id: str) -> None:
        """Record what MDA reported: the name, and that it is opened once the agent browses."""
        update: dict[str, object] = {_NAME_KEY: self.browser}
        if self.opened:
            update[_OPENED_KEY] = True
        await _update(thread_id, update)

    async def open(self, thread_id: str) -> BrowserWindow:
        """Open the agent's window for a person, creating the browser if the agent hasn't yet."""
        window = await self.langsmith.open_window(AGENT_WINDOW)
        if not self.opened:
            await _update(thread_id, {_NAME_KEY: self.browser, _OPENED_KEY: True})
        return window


async def _update(thread_id: str, metadata: dict[str, object]) -> None:
    await langgraph_client().threads.update(thread_id=thread_id, metadata=metadata)
    await Topic.THREAD_BROWSERS.invalidate(key=thread_id)


async def _browser_request(
    method: Literal["GET", "POST", "DELETE"],
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
