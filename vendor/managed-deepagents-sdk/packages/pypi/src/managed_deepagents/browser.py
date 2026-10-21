"""Browser use for a managed agent: Stagehand's facade tools on a LangSmith browser.

Each thread gets its own LangSmith browser, named after the deployment and
thread so ``mda delete`` finds it. The Stagehand client lives in this process
and drives a window of that browser through the LangSmith API's CDP proxy (the
window's ``api_cdp_url``). The client is released after every run; the browser,
its tabs and its logins stay until it idles out.

Stagehand's engine is a Chrome extension. The first open puts Stagehand's in
browserd's extensions directory and asks browserd to load it into the running
Chrome. The extension then dials Chrome from inside the browser sandbox, on the
window's own port.

The tools are Stagehand's facade (``stagehand-middleware``): ``run`` code gets a
Playwright-shaped ``page``, ``context`` and ``browser``, and the tools'
descriptions and schemas are the facade's own. Screenshots run code saves go to
the agent's sandbox, under ``/workspace``.

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
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import BaseTool, tool

from managed_deepagents._managed_tools import ManagedToolRuntime
from managed_deepagents.identity import ManagedServerInfo

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
    from langchain_core.messages import ContentBlock
    from langchain_core.runnables import RunnableConfig
    from langgraph.runtime import Runtime
    from langsmith.sandbox import AsyncSandbox
    from stagehand import Page, Stagehand, StagehandBrowser
    from stagehand_middleware.facade import StagehandFacadeTools
    from websockets.asyncio.client import ClientConnection

T = TypeVar("T")
Scope = Literal["thread", "user"]

#: The deployment's client and the browser's extension must be the same release;
#: ``mda`` adds this pin to the deployment's dependencies.
STAGEHAND_VERSION = "4.3.0"

#: Where screenshots run code saves land in the agent's sandbox; run code names
#: them by paths relative to it.
_WORKSPACE = "/workspace"
_SCREENSHOT_PATH = (
    "Also save the image to this absolute path in your sandbox, creating its folder, "
    "e.g. to publish it."
)
#: What the model needs on top of the facade's own instructions.
_INSTRUCTIONS = (
    f"Screenshots that run code saves go to {_WORKSPACE} in your sandbox. Give them a "
    "relative path: `await page.screenshot({ path: 'shots/home.png' })` saves "
    f"{_WORKSPACE}/shots/home.png. An absolute path is refused. To save the screenshot "
    "tool's image instead, give that tool an absolute `path`."
)

_EXTENSIONS_DIR = "/var/lib/browserd/extensions"
_EXTENSION_DIR = f"{_EXTENSIONS_DIR}/stagehand-{STAGEHAND_VERSION}"
#: browserd's windows, each with the port it serves inside the browser sandbox.
_WINDOWS_FILE = "/var/lib/browserd/attachments.json"
_WINDOW_NAME = "mda"
#: The window that shares the thread's sandbox's localhost with every window.
_LOCALHOST_WINDOW = "mda-localhost"
#: The extension's manifest name, which tells its service worker from other extensions'.
_STAGEHAND_EXTENSION = "Stagehand Runtime"
_CDP_TIMEOUT_S = 30
_HTTP_TIMEOUT = httpx.Timeout(90.0)  # creating a browser waits for Chrome
_SETUP_TIMEOUT_S = 300
_LOG = logging.getLogger("mda.browser")

_EXTENSION_ZIP = "/tmp/mda-stagehand-extension.zip"  # noqa: S108 - in the browser sandbox, not here

# A transport that died: reopen and try once more. Anything else (a page that
# threw, a bad selector) goes back to the model with the session left alone.
_DEAD_SESSION = (
    "closed",
    "connection",
    "websocket",
    "window is gone",
    "stagehand is unavailable",
)

#: Ours, in a thread browser's instructions: it shares its sandbox's localhost.
LOCALHOST_NOTE = (
    "Servers running in your sandbox are reachable at http://localhost:<port> "
    "(any port but 80 and 443)."
)


class _Attachment(TypedDict):
    """A browser window, as the browsers API describes it."""

    id: str
    name: str
    api_cdp_url: str


class _SavedWindow(TypedDict):
    id: str
    port: int


class _SavedWindows(TypedDict, total=False):
    """browserd's saved windows file."""

    attachments: list[_SavedWindow]


@dataclass
class _Session:
    """Stagehand attached to a thread's window, and the facade's tools over it."""

    browser: StagehandBrowser
    stagehand: Stagehand
    facade: StagehandFacadeTools = field(init=False)
    #: A folder of this process where run code's screenshots wait to go to the sandbox.
    artifacts: str = field(init=False)
    #: Run code called `browser.close()`: release this client once the call is done.
    close_requested: bool = False
    shared: str | None = None  # the sandbox whose localhost the browser shares

    def __post_init__(self) -> None:
        from stagehand_middleware.facade import StagehandFacadeTools  # noqa: PLC0415

        self.artifacts = tempfile.mkdtemp(prefix="mda-browser-")
        self.facade = StagehandFacadeTools(
            self.stagehand,
            artifact_root=self.artifacts,
            on_close_requested=self._request_close,
            quiet_wait=True,
        )

    async def _request_close(self) -> None:
        # In place of the facade's own cleanup, which closes the browser itself.
        self.close_requested = True

    async def page(self, thread: str) -> Page:
        """``thread``'s own tab of a browser threads share."""
        context = self.browser.context
        pages = await context.pages()
        tab = next((p for p in pages if p.page_id == _TABS.get(thread)), None)
        if tab is None:  # its first call, or its tab is gone
            tab = await context.new_page()
            await _close_stalest(pages, _TABS_PER_BROWSER - 1)
        _remember_tab(thread, tab.page_id)
        return tab

    async def close(self) -> None:
        # Never `browser.close()`: on a browser we attached to it sends
        # `Browser.close`. Closing Stagehand frees the extension for the next client.
        with contextlib.suppress(Exception):  # best effort
            await asyncio.wait_for(self.stagehand.close(), 3)
        await _drop(self.browser)
        shutil.rmtree(self.artifacts, ignore_errors=True)


#: Each thread's tab in a browser threads share, by Stagehand page ID: the tab's
#: CDP target ID, which outlives our connection. Kept to the most recent threads.
_TABS: dict[str, str] = {}
_MAX_TABS = 1024
#: Tabs a shared browser keeps open; a thread whose tab was closed gets a new one.
_TABS_PER_BROWSER = 10


def _remember_tab(thread: str, page_id: str) -> None:
    _TABS.pop(thread, None)  # most recent last, so the cap drops the stalest
    _TABS[thread] = page_id
    if len(_TABS) > _MAX_TABS:
        del _TABS[next(iter(_TABS))]


async def _close_stalest(pages: list[Page], keep: int) -> None:
    """Close all but ``keep`` of ``pages``: tabs no thread here uses, then the least recent."""
    recency = {page_id: i for i, page_id in enumerate(_TABS.values())}
    stalest = sorted(pages, key=lambda page: recency.get(page.page_id, -1))
    for page in stalest[: max(len(pages) - keep, 0)]:
        with contextlib.suppress(Exception):  # best effort: it may be gone already
            await page.close()


async def _drop(browser: StagehandBrowser) -> None:
    """Close our CDP connection to ``browser``, without `Browser.close`.

    Stagehand (4.3.0 too) leaves it open, and an open connection holds the browser in use.
    """
    from stagehand.browser import _invalidate_browser  # noqa: PLC0415 - no public equivalent

    with contextlib.suppress(Exception):  # best effort
        await asyncio.wait_for(_invalidate_browser(browser), 3)


def _browser_key(runtime: ManagedToolRuntime | Runtime, scope: Scope) -> str:
    """Whose browser this run uses: its thread's, or its signed-in person's.

    A service principal, such as a schedule's, is shared by all of its runs, so
    under user scope each of those runs gets its thread's browser instead.
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
    """The run's thread: tools carry it on ``runtime.config``, agent hooks ambiently."""
    from langgraph.config import get_config  # noqa: PLC0415

    sources: list[RunnableConfig] = []
    if isinstance(runtime, ManagedToolRuntime):
        sources.append(runtime.config)
    with contextlib.suppress(RuntimeError):  # outside a graph run
        sources.append(get_config())
    for config in sources:
        if thread_id := config.get("configurable", {}).get("thread_id"):
            return str(thread_id)
    msg = "browser use needs a thread_id"
    raise RuntimeError(msg)


def _browser_name(key: str) -> str:
    """The browser for ``key``, under the deployment's prefix like its sandboxes."""
    from managed_deepagents.runtime import (  # noqa: PLC0415
        _managed_deployment_name,
        _managed_sandbox_name,
    )

    if deployment := _managed_deployment_name():
        return _managed_sandbox_name(deployment, f"browser:{key}")
    return f"mda-browser-{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def _credentials() -> dict[str, str]:
    """The deployment's LangSmith API key, and the workspace MDA scopes to."""
    from langsmith.utils import get_env_var  # noqa: PLC0415

    api_key = get_env_var("API_KEY")
    if not api_key:
        msg = "browser use needs the deployment's LangSmith API key"
        raise RuntimeError(msg)
    credentials = {"api_key": api_key}
    if workspace := os.environ.get("LANGSMITH_WORKSPACE_ID"):
        credentials["tenant_id"] = workspace
    return credentials


def _sandboxes_url() -> str:
    """The LangSmith sandboxes API, at the endpoint the SDK's sandbox client uses."""
    from langsmith.utils import get_env_var  # noqa: PLC0415

    endpoint = get_env_var("ENDPOINT") or "https://api.smith.langchain.com"
    return f"{endpoint.rstrip('/')}/v2/sandboxes"


def _browsers_url() -> str:
    """The LangSmith browsers API (beta)."""
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
    """Attach Stagehand to the window of browser ``name``, creating either as needed."""
    from langsmith.sandbox import AsyncSandboxClient  # noqa: PLC0415

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
        # Load it into the running Chrome. A no-op once it is loaded, so calling it on
        # every open also heals a browser whose earlier load never happened.
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

    Tools see the sandbox behind a CompositeBackend, through a lazy handle made fresh
    for each run, so the handle asks the process whether the box is open.
    """
    from managed_deepagents.runtime import _LazyManagedSandboxBackend  # noqa: PLC0415

    backend: object = runtime.backend
    while isinstance(backend, CompositeBackend):
        backend = backend.default
    if isinstance(backend, _LazyManagedSandboxBackend):
        backend = backend.opened()
    if not isinstance(backend, SandboxBackendProtocol) or backend.id.startswith("pending:"):
        return None
    return backend.id


def _localhost_alias(sandbox_id: str) -> str:
    """The browser's host for a sandbox's localhost: ``sb-`` and its id in lowercase base32."""
    label = base64.b32encode(uuid.UUID(sandbox_id).bytes).decode().rstrip("=").lower()
    return f"sb-{label}.localhost"


async def _share_localhost(name: str, sandbox: str) -> None:
    """Let browser ``name`` open ``sandbox``'s servers at ``http://localhost:<port>``.

    One window attached with ``share_localhost`` serves every window of the
    browser, so this adds one beside the agent's instead of reopening it. A
    sandbox replaced under the same name keeps the window's ``sandbox`` but not
    its id, so the window is checked by the id its ``localhost_alias`` names.
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
    """Put the client's own Stagehand extension on the browser's disk, unless it is there.

    Chrome loads extensions only from its own filesystem, so the archive is
    written into the browser sandbox and unpacked there.
    """
    from stagehand import get_extension_archive  # noqa: PLC0415

    present = await box.run(f"test -f {_EXTENSION_DIR}/manifest.json", timeout=_SETUP_TIMEOUT_S)
    if present.exit_code == 0:
        return
    await box.write(_EXTENSION_ZIP, get_extension_archive(), timeout=_SETUP_TIMEOUT_S)
    # Unpacked beside its folder, then moved in, so a half-unpacked copy never
    # passes the manifest check above.
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
        # A client that never released (a crash, a reload, a browser resumed
        # mid-session) leaves its claim in the extension's service worker. It
        # outlives the client, and reloading the extension in place disables it;
        # uninstalling and loading it again clears the claim.
        if "already initialized" not in str(err):
            raise
    await _reinstall_stagehand(cdp_url, headers)
    return await _connect(cdp_url, local_url, headers)


async def _connect(cdp_url: str, local_url: str, headers: dict[str, str]) -> _Session:
    """Connect through the API's CDP proxy and start Stagehand, or drop the connection.

    The extension runs inside the browser sandbox, so it dials Chrome on the
    window's local port rather than the API's URL.
    """
    from stagehand import Stagehand, local_browser  # noqa: PLC0415

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

    async def call(
        self, method: str, params: dict[str, object] | None = None, *, session: str | None = None
    ) -> dict[str, object]:
        command_id = next(self.ids)
        message: dict[str, object] = {"id": command_id, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
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
    """Uninstall Stagehand's extension and load it again from its folder, over CDP.

    That ends its service worker and the claim it holds. browserd's
    /extensions/load does not reload an extension it already loaded once.
    """
    import websockets  # noqa: PLC0415 - installed with stagehand, which is imported lazily

    async with (
        asyncio.timeout(_CDP_TIMEOUT_S),
        websockets.connect(cdp_url, additional_headers=headers) as connection,
    ):
        cdp = _CDP(connection)
        targets = cast("list[dict[str, str]]", (await cdp.call("Target.getTargets"))["targetInfos"])
        for target in targets:
            extension = target["url"].removeprefix("chrome-extension://")
            if target["type"] != "service_worker" or extension == target["url"]:
                continue
            attached = await cdp.call(
                "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
            )
            named = await cdp.call(
                "Runtime.evaluate",
                {"expression": "chrome.runtime.getManifest().name", "returnByValue": True},
                session=str(attached["sessionId"]),
            )
            if cast("dict[str, object]", named["result"]).get("value") == _STAGEHAND_EXTENSION:
                await cdp.call("Extensions.uninstall", {"id": extension.split("/", 1)[0]})
                await cdp.call("Extensions.loadUnpacked", {"path": _EXTENSION_DIR})
                return
    msg = "could not find Stagehand's extension in the browser"
    raise RuntimeError(msg)


def _dead_session(err: Exception) -> bool:
    """Whether ``err`` means the session died, so the call is worth reopening and retrying.

    An error the browser reported back (code that threw, a page that failed to
    load, even one that mentions a closed connection) comes from a live session;
    retrying would run the model's code a second time.
    """
    from stagehand.rpc_client import RPCError  # noqa: PLC0415
    from stagehand_middleware.facade import StagehandFacadeExecutionError  # noqa: PLC0415

    if isinstance(err, (RPCError, StagehandFacadeExecutionError)):
        return False
    return any(marker in str(err).lower() for marker in _DEAD_SESSION)


def _describe(err: Exception) -> str:
    """``err`` for the model: the facade's own messages as they are, others with their type."""
    from stagehand_middleware.facade import (  # noqa: PLC0415
        StagehandFacadeExecutionError,
        StagehandFacadeInputError,
    )

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


#: Release a browser's client after this long without a call. A run that raises
#: or pauses on an interrupt never reaches `aafter_agent`, and an open client
#: holds its browser in use, so the browser would never idle out.
_RELEASE_AFTER_S = 60.0

#: Each browser's Stagehand client, and the lock its calls take turns on. A lock
#: lasts only while a call holds or awaits it.
_SESSIONS: dict[str, _Session] = {}
_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
#: Each browser's pending idle release.
_RELEASES: dict[str, asyncio.Task[None]] = {}


class BrowserMiddleware(AgentMiddleware):
    """Give the agent a browser: ``run``, ``snapshot`` and ``screenshot``."""

    name = "mda-browser"

    def __init__(self, *, scope: Scope = "thread", idle_ttl_seconds: int = 600) -> None:
        """Build the browser tools.

        Args:
            scope: ``"thread"`` gives each thread its own browser; ``"user"``
                shares one per signed-in user across their threads, logins
                included. Each of their threads drives its own tab, and their
                calls take turns.
            idle_ttl_seconds: Stop a browser after this long idle.
        """
        from stagehand_middleware.facade import facade_instructions  # noqa: PLC0415

        super().__init__()
        self.scope: Scope = scope
        self.idle_ttl_seconds = idle_ttl_seconds
        # Process-wide: MDA builds this middleware for every run, and a user's
        # concurrent threads must share their browser's one client.
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
        """Run ``fn(session)`` on the run's active tab, reopening once if the session died.

        Errors come back as text: a browsing failure is something the model can
        route around. Results are not clipped here: deepagents' filesystem
        middleware moves a large one to a file the agent can read back.
        """
        try:
            key = _browser_key(runtime, self.scope)
            # A user's threads share the browser, so each drives its own tab.
            thread = _thread_id(runtime) if key.startswith("user:") else None
        except RuntimeError as err:
            return f"error: {err}"
        # Stagehand serves one client per browser: calls on a browser take turns.
        async with self._locks.setdefault(key, asyncio.Lock()):
            try:
                for attempt in (1, 2):
                    try:
                        name = _browser_name(key)
                        if key not in self._sessions:
                            self._sessions[key] = await _open(name, self.idle_ttl_seconds)
                        session = self._sessions[key]
                        await self._share(session, name, key, runtime)
                        if thread is not None:  # the facade acts on the active tab
                            await session.browser.context.set_active_page(
                                await session.page(thread)
                            )
                        return await fn(session)
                    except Exception as err:  # noqa: BLE001 - recover or report
                        if _dead_session(err) and attempt == 1:
                            await self._release(key)
                            continue
                        return f"error: {_describe(err)}"
            finally:
                session = self._sessions.get(key)
                if session is not None and session.close_requested:
                    await self._release(key)
                self._release_later(key)
        return "error: browser unavailable"

    async def _share(
        self, session: _Session, name: str, key: str, runtime: ManagedToolRuntime
    ) -> None:
        """Share the thread's sandbox's localhost with its browser, once that sandbox exists.

        Only a thread's browser: a user's serves several threads, and their
        sandboxes cannot all be ``localhost``. Browsing goes on without it.
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
        """Restart ``key``'s idle countdown, which releases its client when it runs out."""
        if (pending := self._releases.pop(key, None)) is not None:
            pending.cancel()
        self._releases[key] = asyncio.create_task(self._release_when_idle(key))

    async def _release_when_idle(self, key: str) -> None:
        await asyncio.sleep(_RELEASE_AFTER_S)
        # A call holding the lock restarts the countdown before it lets go,
        # cancelling this one.
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
        from stagehand_middleware.facade import facade_tools  # noqa: PLC0415

        middleware = self
        # The facade's names, descriptions and schemas verbatim, as its TypeScript
        # hosts advertise them. The model's arguments are not checked against a JSON
        # schema, so each tool turns away what its schema does not name.
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
    """``request`` with ``text`` as a block of its own at the end of the system message.

    Unlike memory's block it sets no cache breakpoint: Anthropic allows four, and the
    message-level one already covers this static text.
    """
    from langchain_core.messages import SystemMessage  # noqa: PLC0415

    existing = request.system_message
    blocks: list[ContentBlock] = list(existing.content_blocks if existing is not None else [])
    blocks.append({"type": "text", "text": text})
    return request.override(system_message=SystemMessage(content_blocks=blocks))


def _unexpected(arguments: dict[str, object]) -> str:
    names = ", ".join(repr(name) for name in arguments)
    return f"error: unexpected argument{'s' if len(arguments) > 1 else ''} {names}."


async def _save_artifacts(runtime: ManagedToolRuntime, root: str) -> list[str]:
    """Move the screenshots run code saved under ``root`` into the agent's sandbox.

    Run code names them relative to the facade's artifact root, which here stands
    for the sandbox's ``/workspace``. Each note says where one went, or why not.
    """
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
