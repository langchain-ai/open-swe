"""Browser use for a managed agent: Stagehand's facade tools on a LangSmith browser.

Each thread gets a LangSmith browser named after the deployment and thread, so
``mda delete`` finds it. The Stagehand client lives in this process and drives
one window of it through the API's CDP proxy. Stagehand's engine is a Chrome
extension, which MDA installs into the browser and which dials Chrome on the
window's own port. The tools are the facade's (``stagehand-middleware``), with
its descriptions and schemas. Screenshots run code saves go to the agent's
sandbox, under ``/workspace``.

    # browser.py
    browser = define_browser(scope="user")
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import itertools
import json
import logging
import os
import posixpath
import shlex
import shutil
import tempfile
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, TypeVar, cast

import httpx
import websockets
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import BaseTool, tool
from langchain_core.messages import SystemMessage
from langsmith.sandbox import AsyncSandboxClient
from langsmith.utils import get_env_var
from stagehand import Stagehand, get_extension_archive, local_browser
from stagehand.browser import _invalidate_browser
from stagehand.rpc_client import RPCError
from stagehand_middleware.facade import (
    StagehandFacadeExecutionError,
    StagehandFacadeInputError,
    StagehandFacadeTools,
    facade_instructions,
    facade_tools,
)

# At runtime: ToolNode resolves the tools' `runtime` annotation to know what to inject.
from managed_deepagents._managed_tools import ManagedToolRuntime  # noqa: TC001
from managed_deepagents.identity import ManagedServerInfo
from managed_deepagents.runtime import (
    _LazyManagedSandboxBackend,
    _managed_deployment_name,
    _managed_sandbox_name,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
    from langchain_core.messages import ContentBlock
    from langgraph.runtime import Runtime
    from langsmith.sandbox import AsyncSandbox
    from stagehand import Page, StagehandBrowser
    from websockets.asyncio.client import ClientConnection

T = TypeVar("T")
Scope = Literal["thread", "user"]

#: The client and the browser's extension must be the same release; ``mda`` pins it.
STAGEHAND_VERSION = "4.3.0"

#: Where run code's screenshots land in the sandbox; run code names them relative to it.
_WORKSPACE = "/workspace"
_SCREENSHOT_PATH = (
    "Only if you have a sandbox: also save the image to this absolute path in it, "
    "creating its folder, e.g. to publish it."
)
#: A thread browser's, on top: it shares its sandbox's localhost.
LOCALHOST_NOTE = (
    "Servers running in your sandbox are reachable at http://localhost:<port> "
    "(any port but 80 and 443)."
)
#: What the model needs on top of the facade's instructions.
_INSTRUCTIONS = (
    f"Screenshots that run code saves go to {_WORKSPACE} in your sandbox. Give them a "
    "relative path: `await page.screenshot({ path: 'shots/home.png' })` saves "
    f"{_WORKSPACE}/shots/home.png. An absolute path is refused. To save the screenshot "
    "tool's image instead, give that tool an absolute `path`."
)

_EXTENSIONS_DIR = "/var/lib/browserd/extensions"
_EXTENSION_DIR = f"{_EXTENSIONS_DIR}/stagehand-{STAGEHAND_VERSION}"
_EXTENSION_ZIP = "/tmp/mda-stagehand-extension.zip"  # noqa: S108 - in the browser sandbox, not here
_STAGEHAND_EXTENSION = "Stagehand Runtime"  # its manifest name
#: browserd's windows, each with the port it serves inside the browser sandbox.
_WINDOWS_FILE = "/var/lib/browserd/attachments.json"
_WINDOW_NAME = "mda"
#: The window that shares the thread's sandbox's localhost with every window.
_LOCALHOST_WINDOW = "mda-localhost"
_CDP_TIMEOUT_S = 30
_HTTP_TIMEOUT = httpx.Timeout(90.0)  # creating a browser waits for Chrome
_SETUP_TIMEOUT_S = 300
_LOG = logging.getLogger("mda.browser")


class _Attachment(TypedDict):
    id: str
    name: str
    api_cdp_url: str


class _SavedWindow(TypedDict):
    id: str
    port: int


class _SavedWindows(TypedDict, total=False):
    attachments: list[_SavedWindow]


@dataclass
class _Session:
    """Stagehand on a browser's window, and the facade's tools over it."""

    browser: StagehandBrowser
    stagehand: Stagehand
    facade: StagehandFacadeTools = field(init=False)
    #: Where run code's screenshots wait to go to the sandbox, never this process's cwd.
    artifacts: str = field(init=False)
    #: Run code called `browser.close()`: release this client after the call.
    close_requested: bool = False
    #: The thread whose own tab the tools act on, in a browser threads share.
    thread: str | None = None
    shared: str | None = None  # the sandbox whose localhost the browser shares

    def __post_init__(self) -> None:
        self.artifacts = tempfile.mkdtemp(prefix="mda-browser-")
        self.facade = StagehandFacadeTools(
            self.stagehand,
            artifact_root=self.artifacts,
            on_close_requested=self._request_close,  # the default closes the browser
            quiet_wait=True,
        )
        # The facade acts on Chrome's focused tab, which is often another window's (the
        # shared localhost one) that this client cannot see. It has no hook for this.
        self.facade._active_page = self.tab  # ty: ignore[invalid-assignment]  # noqa: SLF001

    async def _request_close(self) -> None:
        self.close_requested = True

    async def tab(self) -> Page:
        """The tab the tools act on: the thread's own, else the window's newest."""
        if self.thread is not None:
            return await self.page(self.thread)
        context = self.browser.context
        pages = await context.pages()
        return pages[-1] if pages else await context.new_page()

    async def page(self, thread: str) -> Page:
        """``thread``'s own tab of a browser threads share, opened on its first call."""
        context = self.browser.context
        pages = await context.pages()
        tab = next((p for p in pages if p.page_id == _TABS.get(thread)), None)
        if tab is None:
            tab = await context.new_page()
            await _close_stalest(pages, _TABS_PER_BROWSER - 1)
        _remember_tab(thread, tab.page_id)
        return tab

    async def close(self) -> None:
        # Never `browser.close()`, which closes the browser itself.
        with contextlib.suppress(Exception):  # best effort
            await asyncio.wait_for(self.stagehand.close(), 3)
        await _drop(self.browser)
        shutil.rmtree(self.artifacts, ignore_errors=True)


#: Each thread's tab by Stagehand page ID (its CDP target ID, which outlives our
#: connection), most recent last.
_TABS: dict[str, str] = {}
_MAX_TABS = 1024
_TABS_PER_BROWSER = 10


def _remember_tab(thread: str, page_id: str) -> None:
    _TABS.pop(thread, None)
    _TABS[thread] = page_id
    if len(_TABS) > _MAX_TABS:
        del _TABS[next(iter(_TABS))]


async def _close_stalest(pages: list[Page], keep: int) -> None:
    """Close all but ``keep`` of ``pages``: untracked tabs first, then the least recent."""
    recency = {page_id: i for i, page_id in enumerate(_TABS.values())}
    stalest = sorted(pages, key=lambda page: recency.get(page.page_id, -1))
    for page in stalest[: max(len(pages) - keep, 0)]:
        with contextlib.suppress(Exception):  # it may be gone already
            await page.close()


async def _drop(browser: StagehandBrowser) -> None:
    """Close our CDP connection, which Stagehand leaves open and which holds the browser."""
    with contextlib.suppress(Exception):  # best effort
        await asyncio.wait_for(_invalidate_browser(browser), 3)


def _browser_key(runtime: ManagedToolRuntime | Runtime, scope: Scope) -> str:
    """Whose browser this run uses: its thread's, or its signed-in person's.

    A service principal, such as a schedule's, is shared by all its runs, so each
    of them gets its thread's browser.
    """
    if scope == "thread":
        return f"thread:{_thread_id(runtime)}"
    info = runtime.server_info
    principal = info.principal if isinstance(info, ManagedServerInfo) else None
    if principal is None:
        msg = 'browser scope "user" needs a signed-in user'
        raise RuntimeError(msg)
    if principal.kind != "person":
        return f"thread:{_thread_id(runtime)}"
    return f"user:{principal.id}"


def _thread_id(runtime: ManagedToolRuntime | Runtime) -> str:
    if (info := runtime.execution_info) and info.thread_id:
        return str(info.thread_id)
    msg = "browser use needs a thread_id"
    raise RuntimeError(msg)


def _browser_name(key: str) -> str:
    """The browser for ``key``, under the deployment's prefix like its sandboxes."""
    if deployment := _managed_deployment_name():
        return _managed_sandbox_name(deployment, f"browser:{key}")
    return f"mda-browser-{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def _credentials() -> dict[str, str]:
    """The deployment's LangSmith API key, and the workspace MDA scopes to."""
    api_key = get_env_var("API_KEY")
    if not api_key:
        msg = "browser use needs the deployment's LangSmith API key"
        raise RuntimeError(msg)
    credentials = {"api_key": api_key}
    if workspace := os.environ.get("LANGSMITH_WORKSPACE_ID"):
        credentials["tenant_id"] = workspace
    return credentials


def _sandboxes_url() -> str:
    endpoint = get_env_var("ENDPOINT") or "https://api.smith.langchain.com"
    return f"{endpoint.rstrip('/')}/v2/sandboxes"


def _browsers_url() -> str:
    return f"{_sandboxes_url()}/browsers"


def _check(resp: httpx.Response, action: str) -> httpx.Response:
    if resp.status_code == httpx.codes.NOT_IMPLEMENTED:
        msg = f"could not {action}: the browser beta is not enabled for this workspace"
        raise RuntimeError(msg)
    if not resp.is_success:
        msg = f"could not {action}: HTTP {resp.status_code} {resp.text[:300]}"
        raise RuntimeError(msg)
    return resp


async def _open(name: str, idle_ttl_seconds: int) -> _Session:
    """Attach Stagehand to browser ``name``'s window, creating either as needed."""
    credentials = _credentials()
    tenant = {"X-Tenant-Id": credentials["tenant_id"]} if "tenant_id" in credentials else {}
    headers = {"X-Api-Key": credentials["api_key"], **tenant}
    base = f"{_browsers_url()}/{name}"
    api = httpx.AsyncClient(headers=headers, timeout=_HTTP_TIMEOUT)
    sandboxes = AsyncSandboxClient(
        api_key=credentials["api_key"], **({"headers": tenant} if tenant else {})
    )
    async with api, sandboxes:
        made = await api.post(
            _browsers_url(), json={"name": name, "idle_ttl_seconds": idle_ttl_seconds}
        )
        if made.status_code != httpx.codes.CONFLICT:  # conflict: it already exists
            _check(made, "create the browser")
        box = await sandboxes.get_sandbox(name)
        if box.status == "stopped":
            box = await sandboxes.start_sandbox(name, timeout=_SETUP_TIMEOUT_S) or box

        await _install_extension(box)
        # A no-op once loaded, so loading on every open heals a browser whose load failed.
        _check(await api.post(f"{base}/extensions/load"), "load the Stagehand extension")

        found = _check(await api.get(base), "look up the browser").json().get("attachments")
        windows = cast("list[_Attachment]", found or [])
        window = next((w for w in windows if w["name"] == _WINDOW_NAME), None)
        if window is None:
            body = {"name": _WINDOW_NAME, "controller": "agent"}
            made = _check(await api.post(f"{base}/attachments", json=body), "open a window")
            window = cast("_Attachment", made.json())
        port = await _window_port(box, window["id"])
        version = await api.get(f"{window['api_cdp_url']}/json/version")
        ws_url: str = _check(version, "reach the browser window").json()["webSocketDebuggerUrl"]

        local_url = f"ws://127.0.0.1:{port}/devtools/browser/{window['id']}"
        return await _attach(ws_url, local_url, headers)


def _thread_sandbox(runtime: ManagedToolRuntime) -> str | None:
    """The run's sandbox once MDA has opened it; the browser never opens one.

    Tools see it behind a CompositeBackend, through a lazy handle made fresh for each
    run, so the handle asks the process whether the box is open.
    """
    backend: object = runtime.backend
    while isinstance(backend, CompositeBackend):
        backend = backend.default
    if isinstance(backend, _LazyManagedSandboxBackend):
        backend = backend.opened()
    if not isinstance(backend, SandboxBackendProtocol) or backend.id.startswith("pending:"):
        return None
    return backend.id


def _localhost_alias(sandbox_id: str) -> str:
    """The browser's host for a sandbox's localhost: ``sb-`` and its id in base32."""
    label = base64.b32encode(uuid.UUID(sandbox_id).bytes).decode().rstrip("=").lower()
    return f"sb-{label}.localhost"


async def _share_localhost(name: str, sandbox: str) -> None:
    """Let browser ``name`` open ``sandbox``'s servers at ``http://localhost:<port>``.

    One window attached with ``share_localhost`` serves the whole browser. A sandbox
    MDA replaced under the same name keeps the window's ``sandbox`` but not its id,
    so the window is checked by the id its ``localhost_alias`` names.
    """
    credentials = _credentials()
    tenant = {"X-Tenant-Id": credentials["tenant_id"]} if "tenant_id" in credentials else {}
    headers = {"X-Api-Key": credentials["api_key"], **tenant}
    base = f"{_browsers_url()}/{name}"
    async with httpx.AsyncClient(headers=headers, timeout=_HTTP_TIMEOUT) as api:
        found = _check(await api.get(base), "look up the browser").json().get("attachments")
        window = next((w for w in found or [] if w.get("name") == _LOCALHOST_WINDOW), None)
        if window is not None:
            box = _check(
                await api.get(f"{_sandboxes_url()}/boxes/{sandbox}"), "look up the sandbox"
            )
            if window.get("localhost_alias") == _localhost_alias(box.json()["id"]):
                return
            stale = f"{base}/attachments/{window['id']}"
            _check(await api.delete(stale), "drop the stale localhost window")
        body = {"name": _LOCALHOST_WINDOW, "sandbox": sandbox, "share_localhost": True}
        _check(await api.post(f"{base}/attachments", json=body), "share the sandbox's localhost")


async def _install_extension(box: AsyncSandbox) -> None:
    """Unpack the client's Stagehand extension on the browser's disk, unless it is there.

    Chrome loads extensions only from its own filesystem.
    """
    present = await box.run(f"test -f {_EXTENSION_DIR}/manifest.json", timeout=_SETUP_TIMEOUT_S)
    if present.exit_code == 0:
        return
    await box.write(_EXTENSION_ZIP, get_extension_archive(), timeout=_SETUP_TIMEOUT_S)
    # Unpacked beside its folder, then moved in, so a half-unpacked copy never passes.
    unpacked = await box.run(
        f"rm -rf {_EXTENSION_DIR} {_EXTENSION_DIR}.tmp && mkdir -p {_EXTENSIONS_DIR} && "
        f"python3 -m zipfile -e {_EXTENSION_ZIP} {_EXTENSION_DIR}.tmp && "
        f"mv {_EXTENSION_DIR}.tmp {_EXTENSION_DIR}",
        timeout=_SETUP_TIMEOUT_S,
    )
    if unpacked.exit_code != 0:
        msg = f"could not install Stagehand in the browser: {unpacked.stderr[-500:]}"
        raise RuntimeError(msg)


async def _attach(cdp_url: str, local_url: str, headers: dict[str, str]) -> _Session:
    """Start Stagehand on the window, taking over a claim a dead client left behind."""
    try:
        return await _connect(cdp_url, local_url, headers)
    except Exception as err:
        # A client that never released (a crash, a reload) leaves its claim in the
        # extension's service worker; reinstalling the extension clears it.
        if "already initialized" not in str(err):
            raise
    await _reinstall_stagehand(cdp_url, headers)
    return await _connect(cdp_url, local_url, headers)


async def _connect(cdp_url: str, local_url: str, headers: dict[str, str]) -> _Session:
    """Connect through the API's CDP proxy and start Stagehand, or drop the connection.

    The extension runs in the browser sandbox, so it dials the window's local port.
    """
    browser = await local_browser.connect(
        cdp_url=cdp_url, extension_cdp_url=local_url, headers=headers
    )
    try:
        stagehand = await Stagehand.create(browser=browser)
    except BaseException:
        await _drop(browser)
        raise
    return _Session(browser, stagehand)


@dataclass
class _CDP:
    """CDP commands over one browser WebSocket."""

    connection: ClientConnection
    ids: itertools.count[int] = field(default_factory=itertools.count)

    async def call(self, method: str, **params: object) -> dict[str, object]:
        command_id = next(self.ids)
        message = {"id": command_id, "method": method, "params": params}
        await self.connection.send(json.dumps(message))
        while True:  # events and other replies share the socket
            reply = cast("dict[str, object]", json.loads(await self.connection.recv()))
            if reply.get("id") != command_id:
                continue
            if "error" in reply:
                msg = f"CDP {method} failed: {reply['error']}"
                raise RuntimeError(msg)
            return cast("dict[str, object]", reply.get("result", {}))


async def _reinstall_stagehand(cdp_url: str, headers: dict[str, str]) -> None:
    """Uninstall Stagehand's extension and load it again, which ends its claim.

    browserd's /extensions/load does not reload an extension it loaded once.
    """
    async with (
        asyncio.timeout(_CDP_TIMEOUT_S),
        websockets.connect(cdp_url, additional_headers=headers) as connection,
    ):
        cdp = _CDP(connection)
        loaded = (await cdp.call("Extensions.getExtensions"))["extensions"]
        for extension in cast("list[dict[str, str]]", loaded):
            if extension["name"] == _STAGEHAND_EXTENSION:
                await cdp.call("Extensions.uninstall", id=extension["id"])
                await cdp.call("Extensions.loadUnpacked", path=_EXTENSION_DIR)
                return
    msg = "could not find Stagehand's extension in the browser"
    raise RuntimeError(msg)


def _from_the_browser(err: Exception) -> bool:
    """Whether a live session reported ``err``; any other error may mean it died."""
    errors = (RPCError, StagehandFacadeExecutionError, StagehandFacadeInputError)
    return isinstance(err, errors)


def _describe(err: Exception) -> str:
    """``err`` for the model: the facade's messages as they are, others with their type."""
    if isinstance(err, StagehandFacadeInputError):
        return str(err)
    if isinstance(err, StagehandFacadeExecutionError):  # thrown by run code: its JS name
        return f"{err.name}: {err}"
    return f"{type(err).__name__}: {err}"


async def _window_port(box: AsyncSandbox, window_id: str) -> int:
    """The port a window serves inside the browser sandbox; browserd saves it on open."""
    for _ in range(20):
        result = await box.run(f"cat {_WINDOWS_FILE}", timeout=60)
        with contextlib.suppress(ValueError):
            saved = cast("_SavedWindows", json.loads(result.stdout))
            for window in saved.get("attachments", []):
                if window["id"] == window_id:
                    return window["port"]
        await asyncio.sleep(0.25)
    msg = "could not find the browser window's port"
    raise RuntimeError(msg)


#: Release a client after this long without a call. A run that raises or pauses on
#: an interrupt never reaches `aafter_agent`, and an open client keeps its browser busy.
_RELEASE_AFTER_S = 60.0

#: Each browser's client, the lock its calls take turns on (kept only while held or
#: awaited), and its pending idle release. Process-wide: MDA builds the middleware for
#: every run, and a user's concurrent threads share one client.
_SESSIONS: dict[str, _Session] = {}
_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_RELEASES: dict[str, asyncio.Task[None]] = {}


class BrowserMiddleware(AgentMiddleware):
    """Give the agent a browser: ``run``, ``snapshot`` and ``screenshot``."""

    name = "mda-browser"

    def __init__(self, *, scope: Scope = "thread", idle_ttl_seconds: int = 600) -> None:
        """Build the browser tools.

        Args:
            scope: ``"thread"`` gives each thread its own browser; ``"user"`` shares
                one per signed-in person, logins included, with a tab per thread.
            idle_ttl_seconds: Stop a browser after this long idle.
        """
        super().__init__()
        self.scope: Scope = scope
        self.idle_ttl_seconds = idle_ttl_seconds
        self._sessions = _SESSIONS
        self._locks = _LOCKS
        self._releases = _RELEASES
        self.tools = self._build_tools()
        self._instructions = f"{facade_instructions()}\n\n{_INSTRUCTIONS}"
        if scope == "thread":
            self._instructions += f" {LOCALHOST_NOTE}"

    async def _call(
        self, runtime: ManagedToolRuntime, fn: Callable[[_Session], Awaitable[T]]
    ) -> T | str:
        """Run ``fn(session)`` on the run's tab, opening the session first if needed.

        Errors go back to the model as text. One the browser did not report releases
        the session, and the next call reconnects; nothing is retried, so no code runs
        twice. deepagents moves a large result to a file, so none is clipped here.
        """
        try:
            key = _browser_key(runtime, self.scope)
            thread = _thread_id(runtime) if key.startswith("user:") else None
        except RuntimeError as err:
            return f"error: {err}"
        # Stagehand serves one client per browser, so calls on a browser take turns.
        async with self._locks.setdefault(key, asyncio.Lock()):
            try:
                name = _browser_name(key)
                if key not in self._sessions:
                    self._sessions[key] = await _open(name, self.idle_ttl_seconds)
                session = self._sessions[key]
                await self._share(session, name, key, runtime)
                session.thread = thread
                return await fn(session)
            except Exception as err:  # noqa: BLE001 - reported to the model
                if not _from_the_browser(err):
                    await self._release(key)
                return f"error: {_describe(err)}"
            finally:
                session = self._sessions.get(key)
                if session is not None and session.close_requested:
                    await self._release(key)
                self._release_later(key)

    async def _share(
        self, session: _Session, name: str, key: str, runtime: ManagedToolRuntime
    ) -> None:
        """Share the thread's sandbox's localhost with its browser, once that sandbox exists.

        Only a thread's browser: a user's serves several threads, whose sandboxes cannot
        all be ``localhost``. Browsing goes on without it.
        """
        sandbox = _thread_sandbox(runtime) if key.startswith("thread:") else None
        if sandbox is None or session.shared == sandbox:
            return
        try:
            await _share_localhost(name, sandbox)
        except Exception as err:  # noqa: BLE001 - tried again on the next call
            _LOG.warning("could not share sandbox %s's localhost with %s: %s", sandbox, name, err)
            return
        session.shared = sandbox

    async def _release(self, key: str) -> None:
        if (session := self._sessions.pop(key, None)) is not None:
            await session.close()

    def _release_later(self, key: str) -> None:
        """Restart ``key``'s idle countdown."""
        if (pending := self._releases.pop(key, None)) is not None:
            pending.cancel()
        self._releases[key] = asyncio.create_task(self._release_when_idle(key))

    async def _release_when_idle(self, key: str) -> None:
        await asyncio.sleep(_RELEASE_AFTER_S)
        # A call holding the lock restarts the countdown before it lets go.
        async with self._locks.setdefault(key, asyncio.Lock()):
            self._releases.pop(key, None)
            await self._release(key)

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> None:  # noqa: ARG002
        """Release the client between runs, so it never idles out holding the extension."""
        with contextlib.suppress(RuntimeError):  # no thread or user, so no session
            key = _browser_key(runtime, self.scope)
            async with self._locks.setdefault(key, asyncio.Lock()):
                await self._release(key)

    def wrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[object]],
    ) -> ModelResponse[object]:
        """Tell the model how the browser works, after the agent's own instructions."""
        return handler(_with_instructions(request, self._instructions))

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelResponse[object]:
        """Tell the model how the browser works, after the agent's own instructions."""
        return await handler(_with_instructions(request, self._instructions))

    def _build_tools(self) -> list[BaseTool]:  # noqa: C901 - three flat tool closures
        middleware = self
        # The facade's names, descriptions and schemas, as its TypeScript hosts advertise
        # them. JSON-schema tools are not checked, so each turns away arguments it lacks.
        contract = {entry["name"]: entry for entry in facade_tools()}
        shot_schema = contract["screenshot"]["inputSchema"]
        shot_schema["properties"]["path"] = {"type": "string", "description": _SCREENSHOT_PATH}

        def facade_tool(name: str) -> Callable[[Callable[..., Awaitable[object]]], BaseTool]:
            entry = contract[name]
            return tool(name, description=entry["description"], args_schema=entry["inputSchema"])

        @facade_tool("snapshot")
        async def snapshot(
            runtime: ManagedToolRuntime,
            *,
            includeIframes: bool = True,  # noqa: N803 - contract name
            **unexpected: object,
        ) -> str:
            if unexpected:
                return _unexpected(unexpected)

            async def go(session: _Session) -> str:
                return await session.facade.snapshot(include_iframes=includeIframes)

            return await middleware._call(runtime, go)

        @facade_tool("run")
        async def run(
            runtime: ManagedToolRuntime,
            code: str | None = None,
            actions: list[dict[str, object]] | None = None,
            **unexpected: object,
        ) -> str:
            if unexpected:
                return _unexpected(unexpected)
            if (code is None) == (actions is None):
                return "error: provide exactly one of `code` or `actions`."

            async def go(session: _Session) -> str:
                try:
                    value = await session.facade.run(code=code, actions=actions)
                finally:  # code that threw may have saved some first
                    saved = await _save_artifacts(runtime, session.artifacts)
                return "\n".join([json.dumps(value, default=str, separators=(",", ":")), *saved])

            return await middleware._call(runtime, go)

        @facade_tool("screenshot")
        async def screenshot(
            runtime: ManagedToolRuntime,
            *,
            fullPage: bool | None = None,  # noqa: N803 - contract name
            type: Literal["png", "jpeg"] = "png",
            quality: float | None = None,
            path: str | None = None,
            **unexpected: object,
        ) -> list[dict[str, str]] | str:
            if unexpected:
                return _unexpected(unexpected)

            async def go(session: _Session) -> dict[str, str]:
                return await session.facade.screenshot(
                    full_page=fullPage, type=type, quality=quality
                )

            shot = await middleware._call(runtime, go)
            if isinstance(shot, str):
                return shot
            text = "Screenshot of the current page."
            if path is not None:
                text = f"{text} {await _save(runtime, path, base64.b64decode(shot['data']))}"
            return [
                {"type": "text", "text": text},
                {"type": "image", "url": f"data:{shot['mimeType']};base64,{shot['data']}"},
            ]

        return [snapshot, run, screenshot]


def _with_instructions(request: ModelRequest[None], text: str) -> ModelRequest[None]:
    """``request`` with ``text`` as its system message's last block.

    No cache breakpoint of its own: Anthropic allows four, and the message's covers it.
    """
    existing = request.system_message
    blocks: list[ContentBlock] = list(existing.content_blocks if existing is not None else [])
    blocks.append({"type": "text", "text": text})
    return request.override(system_message=SystemMessage(content_blocks=blocks))


def _unexpected(arguments: dict[str, object]) -> str:
    names = ", ".join(repr(name) for name in arguments)
    return f"error: unexpected argument{'s' if len(arguments) > 1 else ''} {names}."


async def _save_artifacts(runtime: ManagedToolRuntime, root: str) -> list[str]:
    """Move run code's screenshots from ``root`` to the same paths under ``/workspace``."""
    files = await asyncio.to_thread(_take_files, Path(root))
    return [await _save(runtime, posixpath.join(_WORKSPACE, path), data) for path, data in files]


def _take_files(root: Path) -> list[tuple[str, bytes]]:
    """The files under ``root`` by relative path, deleting each once read."""
    files: list[tuple[str, bytes]] = []
    for file in sorted(path for path in root.rglob("*") if path.is_file()):
        files.append((file.relative_to(root).as_posix(), file.read_bytes()))
        file.unlink()
    return files


async def _save(runtime: ManagedToolRuntime, path: str, data: bytes) -> str:
    """Write ``data`` to ``path`` in the agent's sandbox, as its ``write_file`` would."""
    backend = runtime.backend
    if backend is None:
        return "error: not saved: this agent has no sandbox."
    if not posixpath.isabs(path):
        return f"error: not saved: {path!r} is not an absolute path."
    path = posixpath.normpath(path)
    try:
        if isinstance(backend, SandboxBackendProtocol):  # uploads do not create folders
            await backend.aexecute(f"mkdir -p -- {shlex.quote(posixpath.dirname(path))}")
        [result] = await backend.aupload_files([(path, data)])
    except Exception as err:  # noqa: BLE001 - reported to the model
        return f"error: not saved to {path}: {type(err).__name__}: {err}"
    if result.error:
        return f"error: not saved to {path}: {result.error}"
    return f"Saved to {result.path}."
