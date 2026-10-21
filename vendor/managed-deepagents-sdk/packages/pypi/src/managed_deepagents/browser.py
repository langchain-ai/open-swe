"""Browser use: Stagehand's Python facade tools on a LangSmith browser.

A browser per key (``thread:<id>``, or ``user:<id>`` under user scope). A thread's
own browser has one window, ``mda``; a user's has a window per thread, so their
threads share logins but not tabs. Each
browser has one Stagehand client in this process (the extension serves one at a
time), released between runs and after a minute idle so the browser can idle out.

Only projects that declare ``browser.py`` import this module or install Stagehand.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import posixpath
import shlex
import shutil
import tempfile
import uuid
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import httpx
import websockets
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import BaseTool, tool
from langchain_core.messages import SystemMessage
from langchain_core.messages.content import create_text_block
from langsmith.sandbox import AsyncSandbox, AsyncSandboxClient
from langsmith.utils import get_env_var
from stagehand import BrowserContext, Page, Stagehand, get_extension_archive, local_browser
from stagehand.browser import _invalidate_browser  # no public way to drop the CDP connection
from stagehand.rpc_client import RPCError
from stagehand_middleware.facade import (
    StagehandFacadeExecutionError,
    StagehandFacadeInputError,
    StagehandFacadeTools,
    facade_instructions,
    facade_tools,
)
from stagehand_middleware.facade.tools import sanitize_error_message

from managed_deepagents._managed_tools import ManagedToolRuntime  # noqa: TC001 - tool annotations
from managed_deepagents.identity import ManagedServerInfo
from managed_deepagents.runtime import (
    _LazyManagedSandboxBackend,
    _managed_deployment_name,
    _managed_sandbox_name,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
    from langgraph.runtime import Runtime

T = TypeVar("T")
Scope = Literal["thread", "user"]

#: The client and the browser's extension must match; ``mda`` pins this in deployments.
STAGEHAND_VERSION = "4.3.0"
_WORKSPACE = "/workspace"  # where run code's screenshots land in the sandbox
_INSTRUCTIONS = (
    # The facade's text and errors name its tools `run` and so on; ours gain `browser_`.
    "These tools are named browser_run, browser_snapshot and browser_screenshot: "
    "call those wherever this text or an error names run, snapshot or screenshot.\n"
    f"Screenshots that run code saves go to {_WORKSPACE} in your sandbox: "
    f"`await page.screenshot({{ path: 'shots/a.png' }})` saves {_WORKSPACE}/shots/a.png."
)
_SCREENSHOT_PATH = (
    "Only if you have a sandbox: also save the image to this absolute path in it, "
    "creating its folder, e.g. to publish it."
)
_LOCALHOST_NOTE = (  # a thread's browser shares its sandbox's localhost
    "Servers running in your sandbox are reachable at http://localhost:<port> "
    "(any port but 80 and 443)."
)
_RELEASE_AFTER_S = 60.0  # a run that raises or interrupts never reaches aafter_agent
_LOG = logging.getLogger("mda.browser")


@dataclass
class _Session:
    window: str
    stagehand: Stagehand
    tools: StagehandFacadeTools
    artifacts: str  # where the facade writes run code's screenshots
    browser: str  # the browser's name, which a later generation may have replaced
    closing: bool = False  # run code called `browser.close()`: released after the call
    # The sandbox whose localhost the browser shares. By handle, not name: one MDA
    # replaces mid-run keeps its name but gets a new handle.
    shared: SandboxBackendProtocol | None = None

    async def close(self) -> None:
        # Not `browser.close()`, which closes the browser. Closing Stagehand frees the
        # extension; dropping the CDP connection lets the browser idle out.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.stagehand.close(), 3)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(_invalidate_browser(self.stagehand.browser), 3)
        shutil.rmtree(self.artifacts, ignore_errors=True)


# Process-wide: a user's concurrent threads share their browser's one client.
_SESSIONS: dict[str, _Session] = {}
_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_RELEASES: dict[str, asyncio.Task[None]] = {}


async def _release(key: str) -> None:
    if session := _SESSIONS.pop(key, None):
        await session.close()


async def _close_after_call(key: str) -> None:
    """Run code's `browser.close()`: release the client once the tool call is done.

    The facade asks before the run tool has moved the screenshots that code saved.
    """
    if session := _SESSIONS.get(key):
        session.closing = True


async def _release_when_idle(key: str) -> None:
    await asyncio.sleep(_RELEASE_AFTER_S)
    async with _LOCKS.setdefault(key, asyncio.Lock()):
        _RELEASES.pop(key, None)
        await _release(key)


class BrowserMiddleware(AgentMiddleware):
    """Give the agent Stagehand's tools, named ``browser_run``, ``browser_snapshot`` and so on."""

    name = "mda-browser"

    def __init__(self, *, scope: Scope = "thread", idle_ttl_seconds: int = 600) -> None:
        super().__init__()
        self.scope: Scope = scope
        self.idle_ttl_seconds = idle_ttl_seconds
        self.tools = self._tools()
        self._instructions = f"{facade_instructions()}\n\n{_INSTRUCTIONS}"
        if scope == "thread":
            self._instructions += f" {_LOCALHOST_NOTE}"

    async def _call(
        self, runtime: ManagedToolRuntime, fn: Callable[[_Session], Awaitable[T]]
    ) -> T | str:
        """Run ``fn`` on the run's session. Errors go to the model; nothing is retried."""
        try:
            thread = _thread_id(runtime)
            key = _browser_key(runtime.server_info, thread, self.scope)
        except RuntimeError as err:
            return f"error: {err}"
        window = _window_name(key, thread)
        async with _LOCKS.setdefault(key, asyncio.Lock()):
            try:
                if (session := _SESSIONS.get(key)) and session.window != window:
                    await _release(key)  # another of the user's threads: move windows
                if key not in _SESSIONS:
                    _SESSIONS[key] = await _open(key, window, self.idle_ttl_seconds)
                await self._share(_SESSIONS[key], _SESSIONS[key].browser, key, runtime)
                return await fn(_SESSIONS[key])
            except StagehandFacadeInputError as err:
                return f"error: {err}"
            except StagehandFacadeExecutionError as err:  # thrown by run code
                return f"error: {err.name}: {err}"
            except Exception as err:  # noqa: BLE001 - reported to the model
                # The connection may be dead, unless the browser answered or the facade
                # turned away run code that does not parse, with a plain SyntaxError.
                if not isinstance(err, RPCError | SyntaxError):
                    await _release(key)
                return f"error: {sanitize_error_message(f'{type(err).__name__}: {err}')}"
            finally:
                if (session := _SESSIONS.get(key)) and session.closing:
                    await _release(key)
                if pending := _RELEASES.pop(key, None):
                    pending.cancel()
                _RELEASES[key] = asyncio.create_task(_release_when_idle(key))

    async def _share(
        self, session: _Session, name: str, key: str, runtime: ManagedToolRuntime
    ) -> None:
        """Share the thread's sandbox's localhost with its browser, once that sandbox exists.

        Runs before every browser call, with browser ``name``; Open SWE extends it to tell
        its dashboard which browser the thread drives. Only a thread's browser shares: a
        user's serves several threads, whose sandboxes cannot all be ``localhost``.
        """
        sandbox = _thread_sandbox(runtime) if key.startswith("thread:") else None
        if sandbox is None or session.shared is sandbox:
            return
        try:
            await _share_localhost(name, sandbox.id)
        except Exception as err:  # noqa: BLE001 - browsing goes on; tried again next call
            _LOG.warning(
                "could not share sandbox %s's localhost with %s: %s", sandbox.id, name, err
            )
            return
        session.shared = sandbox

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> None:  # noqa: ARG002
        """Release the client between runs; the browser and its windows stay."""
        with contextlib.suppress(RuntimeError):  # no thread or user, so no session
            key = _browser_key(runtime.server_info, _thread_id(runtime), self.scope)
            async with _LOCKS.setdefault(key, asyncio.Lock()):
                await _release(key)

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelResponse[object]:
        """Append the facade's instructions to the system message, as their own block."""
        system = request.system_message
        blocks = [
            *(system.content_blocks if system else []),
            create_text_block(self._instructions),
        ]
        return await handler(request.override(system_message=SystemMessage(content_blocks=blocks)))

    def _tools(self) -> list[BaseTool]:  # noqa: C901 - three flat tool closures
        """The facade's tools as its hosts advertise them, names prefixed ``browser_``.

        JSON-schema tools take their arguments unchecked, so each turns away names it
        lacks, such as the Python methods' snake_case ones.
        """
        facade = {entry["name"]: entry for entry in facade_tools()}
        path = {"type": "string", "description": _SCREENSHOT_PATH}
        facade["screenshot"]["inputSchema"]["properties"]["path"] = path

        def advertised(name: str) -> Callable[[Callable[..., Awaitable[object]]], BaseTool]:
            entry = facade[name]
            return tool(
                f"browser_{name}",
                description=entry["description"],
                args_schema=entry["inputSchema"],
            )

        @advertised("run")
        async def run(
            runtime: ManagedToolRuntime,
            code: str | None = None,
            actions: list[dict[str, Any]] | None = None,
            **unexpected: object,
        ) -> str:
            if unexpected:
                return _unexpected(unexpected)

            async def go(session: _Session) -> str:
                try:
                    value = await session.tools.run(code=code, actions=actions)
                finally:  # code that threw may have saved some first
                    saved = await _save_artifacts(runtime, Path(session.artifacts))
                return "\n".join([json.dumps(value, default=str), *saved])

            return await self._call(runtime, go)

        @advertised("snapshot")
        async def snapshot(
            runtime: ManagedToolRuntime,
            includeIframes: bool = True,  # noqa: N803 - the facade's name
            **unexpected: object,
        ) -> str:
            if unexpected:
                return _unexpected(unexpected)
            return await self._call(
                runtime, lambda s: s.tools.snapshot(include_iframes=includeIframes)
            )

        @advertised("screenshot")
        async def screenshot(
            runtime: ManagedToolRuntime,
            fullPage: bool | None = None,  # noqa: N803 - the facade's name
            type: Literal["png", "jpeg"] = "png",
            quality: float | None = None,
            path: str | None = None,
            **unexpected: object,
        ) -> list[dict[str, str]] | str:
            if unexpected:
                return _unexpected(unexpected)
            shot = await self._call(
                runtime,
                lambda s: s.tools.screenshot(full_page=fullPage, type=type, quality=quality),
            )
            if isinstance(shot, str):
                return shot
            text = "Screenshot of the current page."
            if path:
                text += " " + await _save(runtime, path, base64.b64decode(shot["data"]))
            image = {"type": "image", "url": f"data:{shot['mimeType']};base64,{shot['data']}"}
            return [{"type": "text", "text": text}, image]

        return [run, snapshot, screenshot]


def _unexpected(arguments: dict[str, object]) -> str:
    names = ", ".join(repr(name) for name in arguments)
    return f"error: unexpected argument{'s' if len(arguments) > 1 else ''} {names}."


async def _save_artifacts(runtime: ManagedToolRuntime, root: Path) -> list[str]:
    """Move what the facade saved under ``root`` to the same relative path in /workspace."""
    files = await asyncio.to_thread(_take_files, root)
    return [await _save(runtime, f"{_WORKSPACE}/{rel}", data) for rel, data in files]


def _take_files(root: Path) -> list[tuple[str, bytes]]:
    files = [
        (p.relative_to(root).as_posix(), p.read_bytes()) for p in root.rglob("*") if p.is_file()
    ]
    for rel, _ in files:
        (root / rel).unlink()
    return sorted(files)


async def _save(runtime: ManagedToolRuntime, path: str, data: bytes) -> str:
    """Write ``data`` to absolute ``path`` in the agent's sandbox, creating its folder."""
    backend = runtime.backend
    if backend is None:
        return f"error: {path} not saved: this agent has no sandbox."
    if not posixpath.isabs(path):
        return f"error: {path} not saved: give an absolute path."
    path = posixpath.normpath(path)
    try:
        if isinstance(backend, SandboxBackendProtocol):
            await backend.aexecute(f"mkdir -p -- {shlex.quote(posixpath.dirname(path))}")
        [result] = await backend.aupload_files([(path, data)])
    except Exception as err:  # noqa: BLE001 - reported to the model
        return f"error: {path} not saved: {type(err).__name__}: {err}"
    return f"error: {path} not saved: {result.error}" if result.error else f"Saved {path}."


def _browser_key(info: object, thread: str, scope: Scope) -> str:
    """Under user scope, a person's browser; service principals stand for many people."""
    if scope == "user":
        principal = info.principal if isinstance(info, ManagedServerInfo) else None
        if principal is None:
            msg = 'browser scope "user" needs a signed-in user'
            raise RuntimeError(msg)
        if principal.kind == "person":
            return f"user:{principal.id}"
    return f"thread:{thread}"


def _thread_id(runtime: ManagedToolRuntime | Runtime) -> str:
    if (info := runtime.execution_info) and info.thread_id:
        return str(info.thread_id)
    msg = "browser use needs a thread_id"
    raise RuntimeError(msg)


def _thread_sandbox(runtime: ManagedToolRuntime) -> SandboxBackendProtocol | None:
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
    return backend


def _window_name(key: str, thread: str) -> str:
    """A thread's own browser has the one window ``mda``; a user's, one per thread."""
    if key.startswith("thread:"):
        return "mda"
    return f"mda-{hashlib.sha256(thread.encode()).hexdigest()[:16]}"


def _browser_name(key: str, generation: int = 0) -> str:
    """``key``'s browser, under the deployment's prefix like its sandboxes.

    A later ``generation`` replaces a browser that is being deleted or broke.
    """
    scope = f"browser:{key}" if generation == 0 else f"browser:{key}:{generation}"
    if deployment := _managed_deployment_name():
        return _managed_sandbox_name(deployment, scope)
    return f"mda-{hashlib.sha256(scope.encode()).hexdigest()[:16]}"


async def _usable_browser(
    api: httpx.AsyncClient,
    sandboxes: AsyncSandboxClient,
    browsers: str,
    key: str,
    idle_ttl_seconds: int,
) -> tuple[str, AsyncSandbox]:
    """``key``'s browser, created or started as needed, and its sandbox.

    A browser that is being deleted, or broke, can't be used or recreated under
    its name, so ``key`` moves on to its next generation.
    """
    for generation in range(_BROWSER_GENERATIONS):
        name = _browser_name(key, generation)
        try:
            # HiDPI renders at twice the resolution, so a person watching or driving
            # the window sees it sharp; it can only be chosen at creation.
            create = {"name": name, "idle_ttl_seconds": idle_ttl_seconds, "hidpi": True}
            await api.post(browsers, json=create)
        except httpx.HTTPStatusError as err:
            if err.response.status_code != httpx.codes.CONFLICT:  # conflict: it exists
                raise
        box = await sandboxes.get_sandbox(name)
        if box.status in _UNUSABLE:
            continue
        if box.status == "stopped":
            box = await sandboxes.start_sandbox(name, timeout=300) or box
        return name, box
    msg = "could not find or create a usable browser"
    raise RuntimeError(msg)


# --- LangSmith: create the browser, put Stagehand's extension on it, connect -------

_BROWSER_GENERATIONS = 5
_UNUSABLE = {"deleting", "deleted", "failed", "error"}  # browser states to move past
_EXTENSION_DIR = f"/var/lib/browserd/extensions/stagehand-{STAGEHAND_VERSION}"
_EXTENSION_ZIP = "/tmp/mda-stagehand.zip"  # noqa: S108 - in the browser sandbox
_WINDOWS_FILE = "/var/lib/browserd/attachments.json"  # browserd's windows and their ports
_LOCALHOST_WINDOW = "mda-localhost"  # shares a sandbox's localhost with every window


def _api() -> tuple[str, dict[str, str]]:
    """LangSmith's sandboxes API, and its headers: the key, and the workspace if set."""
    tenant = {"X-Tenant-Id": ws} if (ws := os.environ.get("LANGSMITH_WORKSPACE_ID")) else {}
    endpoint = (get_env_var("ENDPOINT") or "https://api.smith.langchain.com").rstrip("/")
    return f"{endpoint}/v2/sandboxes", {"X-Api-Key": get_env_var("API_KEY") or "", **tenant}


async def _open(key: str, window_name: str, idle_ttl_seconds: int) -> _Session:
    """Attach Stagehand to a window of ``key``'s browser, creating either as needed.

    The client reaches Chrome through the API's CDP proxy; the extension, inside the
    browser sandbox, dials the window's local port.
    """
    url, headers = _api()
    browsers = f"{url}/browsers"

    async with (
        httpx.AsyncClient(headers=headers, timeout=90, event_hooks={"response": [_raise]}) as api,
        AsyncSandboxClient(api_key=headers["X-Api-Key"], headers=headers) as sandboxes,
    ):
        name, box = await _usable_browser(api, sandboxes, browsers, key, idle_ttl_seconds)
        await _install_extension(box)
        await api.post(f"{browsers}/{name}/extensions/load")  # a no-op once loaded
        windows = (await api.get(f"{browsers}/{name}")).json().get("attachments") or []
        window = next((w for w in windows if w["name"] == window_name), None)
        if window is None:
            body = {"name": window_name, "controller": "agent"}
            window = (await api.post(f"{browsers}/{name}/attachments", json=body)).json()
        cdp_url = (await api.get(f"{window['api_cdp_url']}/json/version")).json()[
            "webSocketDebuggerUrl"
        ]
        port = await _window_port(box, window["id"])

    local_url = f"ws://127.0.0.1:{port}/devtools/browser/{window['id']}"
    try:
        return await _connect(key, window_name, name, cdp_url, local_url, headers)
    except Exception as err:
        if "already initialized" not in str(err):
            raise
    # A client that never closed (a crash, another replica) left its claim in the
    # extension; reinstalling it clears the claim without restarting Chrome.
    await _reinstall_extension(cdp_url, headers)
    return await _connect(key, window_name, name, cdp_url, local_url, headers)


async def _share_localhost(name: str, sandbox: str) -> None:
    """Let browser ``name`` open ``sandbox``'s servers at ``http://localhost:<port>``.

    One window attached with ``share_localhost`` serves the whole browser. A sandbox
    MDA replaced under the same name keeps the window's ``sandbox`` but not its id,
    so the window is checked by the id its ``localhost_alias`` names.
    """
    url, headers = _api()
    browser = f"{url}/browsers/{name}"
    async with httpx.AsyncClient(
        headers=headers, timeout=90, event_hooks={"response": [_raise]}
    ) as api:
        windows = (await api.get(browser)).json().get("attachments") or []
        if window := next((w for w in windows if w["name"] == _LOCALHOST_WINDOW), None):
            box = (await api.get(f"{url}/boxes/{sandbox}")).json()
            if window.get("localhost_alias") == _localhost_alias(box["id"]):
                return
            await api.delete(f"{browser}/attachments/{window['id']}")  # an old sandbox's
        body = {"name": _LOCALHOST_WINDOW, "sandbox": sandbox, "share_localhost": True}
        await api.post(f"{browser}/attachments", json=body)


def _localhost_alias(sandbox_id: str) -> str:
    """The browser's host for a sandbox's localhost: ``sb-`` and its id in base32."""
    label = base64.b32encode(uuid.UUID(sandbox_id).bytes).decode().rstrip("=").lower()
    return f"sb-{label}.localhost"


async def _raise(response: httpx.Response) -> None:
    if not response.is_success:
        await response.aread()
        msg = f"LangSmith browsers API: HTTP {response.status_code} {response.text[:300]}"
        raise httpx.HTTPStatusError(msg, request=response.request, response=response)


async def _connect(
    key: str, window: str, browser_name: str, cdp_url: str, local_url: str, headers: dict[str, str]
) -> _Session:
    browser = await local_browser.connect(
        cdp_url=cdp_url, extension_cdp_url=local_url, headers=headers
    )
    try:
        stagehand = await Stagehand.create(browser=browser)
    except BaseException:
        with contextlib.suppress(Exception):
            await _invalidate_browser(browser)
        raise
    artifacts = tempfile.mkdtemp(prefix="mda-browser-")
    # Run code's `browser.close()` releases only our client, not the browser.
    tools = StagehandFacadeTools(
        stagehand,
        artifact_root=artifacts,
        on_close_requested=lambda: _close_after_call(key),
        quiet_wait=True,
    )
    # The facade acts on Chrome's focused tab, which is often in a window this client
    # cannot see (another thread's, or the shared localhost one), and `set_active_page`
    # does not move headless Chrome's focus. It has no hook for the page, so name ours.
    tools._active_page = lambda: _newest_tab(browser.context)  # ty: ignore[invalid-assignment]  # noqa: SLF001
    return _Session(window, stagehand, tools, artifacts, browser_name)


async def _newest_tab(context: BrowserContext) -> Page:
    """The window's newest tab, as it was: the client's CDP lists only its window's."""
    pages = await context.pages()
    return pages[-1] if pages else await context.new_page()


async def _install_extension(box: AsyncSandbox) -> None:
    """Unpack the client's own extension into the browser, unless it is there already."""
    if (await box.run(f"test -f {_EXTENSION_DIR}/manifest.json", timeout=60)).exit_code == 0:
        return
    await box.write(_EXTENSION_ZIP, get_extension_archive(), timeout=300)
    tmp = f"{_EXTENSION_DIR}.tmp"  # unpacked aside, so a partial copy never passes the check
    unpacked = await box.run(
        f"rm -rf {tmp} && python3 -m zipfile -e {_EXTENSION_ZIP} {tmp} && "
        f"rm -rf {_EXTENSION_DIR} && mv {tmp} {_EXTENSION_DIR}",
        timeout=300,
    )
    if unpacked.exit_code != 0:
        msg = f"could not install Stagehand in the browser: {unpacked.stderr[-500:]}"
        raise RuntimeError(msg)


async def _reinstall_extension(cdp_url: str, headers: dict[str, str]) -> None:
    """Uninstall Stagehand's extension and load it again over CDP."""
    async with asyncio.timeout(30), websockets.connect(cdp_url, additional_headers=headers) as ws:

        async def cdp(id_: int, method: str, **params: object) -> dict[str, Any]:
            await ws.send(json.dumps({"id": id_, "method": method, "params": params}))
            while (reply := json.loads(await ws.recv())).get("id") != id_:
                pass  # events share the socket
            if "error" in reply:
                msg = f"CDP {method} failed: {reply['error']}"
                raise RuntimeError(msg)
            return reply.get("result", {})

        for extension in (await cdp(0, "Extensions.getExtensions"))["extensions"]:
            if extension["name"] == "Stagehand Runtime":
                await cdp(1, "Extensions.uninstall", id=extension["id"])
        await cdp(2, "Extensions.loadUnpacked", path=_EXTENSION_DIR)


async def _window_port(box: AsyncSandbox, window_id: str) -> int:
    """The port browserd serves a window on inside the browser sandbox, once it is saved."""
    for _ in range(20):
        with contextlib.suppress(ValueError, KeyError, TypeError):
            saved = json.loads((await box.run(f"cat {_WINDOWS_FILE}", timeout=60)).stdout)
            if port := next(
                (w["port"] for w in saved["attachments"] if w["id"] == window_id), None
            ):
                return int(port)
        await asyncio.sleep(0.25)
    msg = "could not find the browser window's port"
    raise RuntimeError(msg)
