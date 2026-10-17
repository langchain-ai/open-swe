"""The LangSmith browsers agents drive from their sandboxes.

A LangSmith browser is a sandbox running one persistent Chrome whose profile
keeps its logins. Each person has one for their private threads and each
workspace one for its threads; a thread's sandbox gets its own window of it over
the Chrome DevTools Protocol, attached with its localhost shared so pages can
open the dev servers the sandbox runs.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Self
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid7

import httpx2
from pydantic import BaseModel, BeforeValidator, ValidationError
from sqlalchemy import ColumnElement, ForeignKey, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.sandboxes.providers.langsmith import sandbox_api_request
from openswe.ui_invalidations.topics import Topic
from openswe.users.records import user_id_for_login
from openswe.utils.thread_ops import langgraph_client
from openswe.workspaces.rows import WorkspaceRow

BrowserScope = Literal["private", "workspace"]

# Creating and starting wait for Chrome to answer.
_BROWSER_START_TIMEOUT_SECONDS = 180.0
_BROWSER_REQUEST_TIMEOUT_SECONDS = 30.0
_GONE_STATUSES = frozenset({"failed", "deleting"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
_THREAD_METADATA_KEY = "browser_window"
# LangSmith's answer for a route it does not have, as opposed to a browser it does not have.
_ROUTE_MISSING = "The requested path does not exist"

logger = logging.getLogger(__name__)


class BrowserUnavailableError(RuntimeError):
    """LangSmith refused the browser beta for this deployment."""


class BrowserGoneError(RuntimeError):
    """The browser no longer exists in LangSmith, or can no longer run."""


class BrowserTab(BaseModel):
    id: str
    title: str = ""
    url: str = ""


def _controller(value: object) -> object:
    # Deployments older than the controller API call a person's control "human".
    return "user" if value == "human" else value


class BrowserWindow(BaseModel):
    """One window of a browser, attached to one sandbox."""

    id: str
    sandbox: str = ""
    cdp_url: str = ""
    localhost_alias: str = ""
    controller: Annotated[Literal["agent", "user"], BeforeValidator(_controller)]
    reason: str = ""
    tabs: list[BrowserTab] = []
    view_url: str = ""

    def localhost_url(self, url: str) -> str:
        """``url`` with a loopback host pointed at this window's sandbox.

        Plain localhost reaches the sandbox only while it is the one sandbox
        sharing its localhost with the browser, which a shared browser never
        promises; ``localhost_alias`` always reaches it.
        """
        parts = urlsplit(url)
        if not self.localhost_alias or parts.hostname is None:
            return url
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        if host not in _LOOPBACK_HOSTS:
            return url
        netloc = self.localhost_alias + (f":{parts.port}" if parts.port else "")
        return urlunsplit(parts._replace(netloc=netloc))


class BrowserState(BaseModel):
    id: str
    name: str
    status: str
    # Listed only while the browser is running.
    attachments: list[BrowserWindow] = []


class _BrowserBox(BaseModel):
    dataplane_url: str | None = None


class LangSmithBrowser:
    """One browser in LangSmith's browser beta, by sandbox name."""

    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    async def create(cls) -> Self:
        response = await _browser_request(
            "POST", "/browsers", json={}, timeout=_BROWSER_START_TIMEOUT_SECONDS
        )
        return cls(BrowserState.model_validate(response.json()).name)

    async def window_for(self, sandbox: str) -> BrowserWindow:
        """The window ``sandbox`` drives, with its localhost shared, attaching one if needed."""
        state = await self._running_state()
        windows = [window for window in state.attachments if window.sandbox == sandbox]
        if windows:
            return max(windows, key=lambda window: bool(window.localhost_alias))
        response = await _browser_request(
            "POST",
            f"/browsers/{self.name}/attachments",
            json={"sandbox": sandbox, "share_localhost": True},
            timeout=_BROWSER_START_TIMEOUT_SECONDS,
        )
        return BrowserWindow.model_validate(response.json())

    async def open_tab(self, window: BrowserWindow, url: str) -> BrowserTab:
        response = await _browser_request(
            "POST",
            f"/browsers/{self.name}/attachments/{window.id}/tabs",
            json={"url": window.localhost_url(url)},
            timeout=_BROWSER_REQUEST_TIMEOUT_SECONDS,
        )
        return BrowserTab.model_validate(response.json())

    async def window(self, window_id: str) -> BrowserWindow:
        """One of this browser's windows, starting the browser when it is stopped."""
        state = await self._running_state()
        for window in state.attachments:
            if window.id == window_id:
                return window
        raise BrowserGoneError(f"window {window_id} of browser {self.name} is closed")

    async def set_controller(
        self, window_id: str, controller: Literal["agent", "user"]
    ) -> BrowserWindow:
        response = await _browser_request(
            "POST",
            f"/browsers/{self.name}/attachments/{window_id}/controller",
            json={"controller": controller},
            timeout=_BROWSER_REQUEST_TIMEOUT_SECONDS,
        )
        return BrowserWindow.model_validate(response.json())

    async def view_socket_url(self, window_id: str, tab_id: str) -> str:
        """The live view of one tab of a window: JPEG frames out, a person's input in.

        It is served beside the browser's own dataplane URL rather than through
        the API, which does not pass WebSocket upgrades.
        """
        response = await _browser_request(
            "GET", f"/boxes/{self.name}", timeout=_BROWSER_REQUEST_TIMEOUT_SECONDS
        )
        dataplane_url = _BrowserBox.model_validate(response.json()).dataplane_url
        if not dataplane_url:
            raise BrowserGoneError(f"browser {self.name} has no dataplane")
        sandboxes_base = dataplane_url.rstrip("/").rsplit("/", 1)[0]
        parts = urlsplit(
            f"{sandboxes_base}/browsers/{quote(self.name, safe='')}"
            f"/attachments/{quote(window_id, safe='')}/view"
        )
        return urlunsplit(
            parts._replace(
                scheme="wss" if parts.scheme == "https" else "ws",
                query=urlencode({"tab": tab_id}),
            )
        )

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
        response = await _browser_request(
            "GET", f"/browsers/{self.name}", timeout=_BROWSER_REQUEST_TIMEOUT_SECONDS
        )
        return BrowserState.model_validate(response.json())


class ThreadBrowserWindow(BaseModel):
    """The browser window a thread's agent opened last, kept in the thread's metadata."""

    scope: BrowserScope
    browser: str
    window: str

    @classmethod
    def of(cls, metadata: Mapping[str, object]) -> Self | None:
        raw = metadata.get(_THREAD_METADATA_KEY)
        if raw is None:
            return None
        try:
            return cls.model_validate(raw)
        except ValidationError:
            logger.warning("Ignoring a malformed thread browser window", exc_info=True)
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
        return await sandbox_api_request(method, path, json=json, timeout=timeout)
    except httpx2.HTTPStatusError as exc:
        if exc.response.status_code == 501:
            raise BrowserUnavailableError(
                "LangSmith's browser beta is not enabled for this deployment's workspace"
            ) from exc
        if exc.response.status_code == 404 and _ROUTE_MISSING in exc.response.text:
            raise BrowserUnavailableError(
                "This LangSmith deployment does not support that browser operation yet"
            ) from exc
        if exc.response.status_code == 404 and path.startswith(("/browsers/", "/boxes/")):
            raise BrowserGoneError(str(exc)) from exc
        raise


class SandboxBrowserRow(Base):
    __tablename__ = "sandbox_browser"

    langsmith_browser: Mapped[str]
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), default=None
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), default=None
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


@dataclass(frozen=True, kw_only=True)
class BrowserOwner:
    """Whose browser a thread uses: one person's, or one workspace's.

    ``actor`` is the person whose run creates or replaces it, recorded as the
    row's author.
    """

    user_id: UUID | None = None
    workspace_id: UUID | None = None
    actor: UUID | None = None

    @classmethod
    async def person(cls, login: str) -> Self:
        async with postgres.session() as session:
            user_id = await session.scalar(select(user_id_for_login(login)))
        if user_id is None:
            raise ValueError(f"{login} has no Open SWE account")
        return cls(user_id=user_id, actor=user_id)

    @classmethod
    async def workspace(cls, slug: str, *, actor_login: str | None) -> Self:
        async with postgres.session() as session:
            workspace_id = await session.scalar(
                select(WorkspaceRow.id).where(WorkspaceRow.slug == slug)
            )
            actor = (
                await session.scalar(select(user_id_for_login(actor_login)))
                if actor_login
                else None
            )
        if workspace_id is None:
            raise ValueError(f"workspace {slug} no longer exists")
        return cls(workspace_id=workspace_id, actor=actor)

    async def browser(self, *, replacing: str | None = None) -> LangSmithBrowser:
        """This owner's browser, created on first use.

        ``replacing`` names a browser the caller found gone: it is replaced unless
        another run already has.
        """
        async with postgres.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"sandbox-browser:{self.user_id or self.workspace_id}"},
            )
            row = await session.scalar(select(SandboxBrowserRow).where(self._is_owner()))
            if row is not None and row.langsmith_browser != replacing:
                return LangSmithBrowser(row.langsmith_browser)
            created = await LangSmithBrowser.create()
            await self._record(session, row, created.name)
            return created

    async def _record(
        self, session: AsyncSession, row: SandboxBrowserRow | None, name: str
    ) -> None:
        if row is None:
            session.add(
                SandboxBrowserRow(
                    langsmith_browser=name,
                    user_id=self.user_id,
                    workspace_id=self.workspace_id,
                    created_by=self.actor,
                    updated_by=self.actor,
                )
            )
            return
        await session.execute(
            update(SandboxBrowserRow)
            .where(SandboxBrowserRow.id == row.id)
            .values(langsmith_browser=name, updated_by=self.actor, updated_at=NOW)
        )

    def _is_owner(self) -> ColumnElement[bool]:
        if self.user_id is not None:
            return SandboxBrowserRow.user_id == self.user_id
        return SandboxBrowserRow.workspace_id == self.workspace_id

    async def window_for(self, sandbox: str) -> tuple[LangSmithBrowser, BrowserWindow]:
        """``sandbox``'s window of this owner's browser, replacing a browser found gone."""
        browser = await self.browser()
        try:
            return browser, await browser.window_for(sandbox)
        except BrowserGoneError:
            browser = await self.browser(replacing=browser.name)
            return browser, await browser.window_for(sandbox)
