"""Browser use: Stagehand's Python facade tools on a LangSmith browser.

A browser per key (``thread:<id>``, or ``user:<id>`` under user scope). A thread's
own browser has one window, ``mda``; a user's has a window per thread, so their
threads share logins but not tabs. Each
browser has one Stagehand client in this process (the extension serves one at a
time), released between runs and after a minute idle so the browser can idle out.

``browser_handoff`` hands the window to the user and returns at once. A run of the
watcher graph, which ``mda`` builds beside the agent, waits on its own thread for the
user to hand it back, then enqueues a run on the agent's thread with a notification,
as an async subagent tells the agent that started it that it finished.

Only projects that declare ``browser.py`` import this module or install Stagehand.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import math
import os
import posixpath
import shlex
import shutil
import tempfile
import time
import uuid
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, NotRequired, TypedDict, TypeVar, cast

import httpx
import websockets
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol
from deepagents.middleware.async_subagents import (
    AsyncSubAgent,
    _forwarded_context,
    _launching_agent,
    _parent_reference,
    _trace_headers,
)
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, OmitFromInput, PrivateStateAttr
from langchain.tools import BaseTool, tool
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.messages.content import create_text_block
from langgraph.config import get_config
from langgraph.graph import START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command
from langgraph_sdk import get_client
from langsmith.sandbox import AsyncSandbox, AsyncSandboxClient, ResourceNotFoundError
from langsmith.utils import get_env_var
from pydantic import TypeAdapter, ValidationError
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

from managed_deepagents._channels.assistant_text import MDA_NOTIFICATION_KEY
from managed_deepagents._managed_tools import ManagedToolRuntime  # noqa: TC001 - tool annotations
from managed_deepagents.define_browser import BROWSER_TOOL_PREFIX
from managed_deepagents.identity import ManagedServerInfo
from managed_deepagents.runtime import (
    _LazyManagedSandboxBackend,
    _managed_deployment_name,
    _managed_sandbox_name,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest

T = TypeVar("T")
_Content = TypeVar("_Content", bound=str | list[Any])  # what a tool returns the model
Scope = Literal["thread", "user"]

#: The client and the browser's extension must match; ``mda`` pins this in deployments.
STAGEHAND_VERSION = "4.3.0"
_WORKSPACE = "/workspace"  # where run code's screenshots land in the agent's files
_INSTRUCTIONS = (
    # The facade's text and errors name its tools `run` and so on; ours gain `browser_`.
    "These tools are named browser_run, browser_snapshot and browser_screenshot: "
    "call those wherever this text or an error names run, snapshot or screenshot.\n"
    f"Screenshots that run code saves go to {_WORKSPACE} in your files: "
    f"`await page.screenshot({{ path: 'shots/a.png' }})` saves {_WORKSPACE}/shots/a.png."
)
_SCREENSHOT_PATH = "Also save the image to this absolute path in your files, creating its folder."
_LOCALHOST_NOTE = (  # a thread's browser shares its sandbox's localhost
    "Servers running in your sandbox are reachable at http://localhost:<port> "
    "(any port but 80 and 443)."
)
_RECORDINGS = f"{_WORKSPACE}/recordings"
# The recorder's own limits, which `_browser_recording` sets: it runs in the browser's
# sandbox, so this process never imports it.
_RECORD_VIEWPORT = (1280, 800)
_RECORD_MAX_HOLD_S = 1.5
_RECORD_MAX_S = 300
_RECORD_DESCRIPTION = f"""Record the browser tab as a video, to show a person what you did.

`start`, act as usual, then `stop`: it saves an H.264 mp4 to the `path` you gave
either, in your sandbox (default {_RECORDINGS}/<time>.mp4), with what happened when
beside it as JSON (the same path, ending .json), and shows you its first and last
frames. The video shows a cursor
gliding to each click, the clicks, and what is typed (masked in password fields). While
recording, the viewport is {_RECORD_VIEWPORT[0]}x{_RECORD_VIEWPORT[1]}, and each
pause between your calls plays for at most {_RECORD_MAX_HOLD_S:g}s.
A recording also ends, and is saved, when the run ends or you hand the browser to
the user: it never shows what they do. It stops at
{_RECORD_MAX_S // 60} minutes. It needs a sandbox to save in."""
_HANDOFF_LINK_TTL_S = 900  # how long the link a user gets for a handoff works
#: The graph `mda` builds beside the agent, whose run waits out a handoff.
HANDOFF_WATCHER_GRAPH = "mda-browser-handoff"
_HAND_BACK_POLL_S = 2.0
_WITH_USER = (
    "The browser is with the user until they hand it back; wait for the notification "
    "that they did before using it."
)
_HANDED_OVER = (  # what the model reads from browser_handoff
    "The browser is with the user. You'll get a notification when they hand it back; "
    "until then, don't use the browser. Tell the user what to do in it."
)
#: Clients such as Studio show a notification's first sentence, so it reads for both.
_WAKE_TEXT: dict[BrowserHandoffStatus, str] = {
    "handed_back": (
        "The browser was handed back. Take a snapshot of the page; if the user hasn't "
        "finished, call browser_handoff again."
    ),
    "ended": "The browser handoff ended. Take a snapshot to see whether the user finished.",
}
_RELEASE_AFTER_S = 60.0  # a run that raises or interrupts never reaches aafter_agent
#: Without a sandbox a saved file lives in thread state, which every later checkpoint carries.
_MAX_SAVED_BYTES = 5_000_000
_REPLACED = (  # before what the call returned, once the thread's browser was made again
    "This is a new browser: the one you used before is gone. It started on a blank page, "
    "signed out of everything, and nothing you or the user did in the old one carried over."
)
_LOST = "error: the browser was deleted, so the recording is lost."
_LOG = logging.getLogger("mda.browser")


BrowserHandoffStatus = Literal["handed_back", "ended"]
"""handed_back when the user hands the window back; ended when the handoff's link ran
out, or its browser or window is gone."""


class PendingBrowserHandoff(TypedDict):
    """A handoff that waits on the user, in the thread's state."""

    browser: str
    """The LangSmith browser's name."""

    window_id: str
    """The window the user has."""

    until: float
    """When the user's link runs out, in epoch seconds."""


def _latest(_: str | None, new: str | None) -> str | None:
    return new


class BrowserState(AgentState):
    """The thread's handoff that waits on the user, as deepagents keeps its async tasks,
    and the browser it last used."""

    mda_browser_handoff: Annotated[NotRequired[PendingBrowserHandoff | None], OmitFromInput]
    mda_browser: Annotated[NotRequired[str | None], PrivateStateAttr, _latest]
    """The browser sandbox's id: one made again under the same name has a new one.
    Calls side by side may each write it."""


@dataclass
class _Session:
    window: str
    stagehand: Stagehand
    tools: StagehandFacadeTools
    artifacts: str  # where the facade writes run code's screenshots
    browser: str  # the browser's name, which a later generation may have replaced
    box: str | None = None  # the browser sandbox's id, which a browser made again changes
    closing: bool = False  # run code called `browser.close()`: released after the call
    # The sandbox whose localhost the browser shares. By handle, not name: one MDA
    # replaces mid-run keeps its name but gets a new handle.
    shared: SandboxBackendProtocol | None = None
    local_url: str = ""  # this window's CDP inside the browser sandbox, for the recorder

    @property
    def disconnected(self) -> bool:
        """Whether the client lost Chrome, as when browserd restarts it: for good.

        Stagehand closes its client once the connection ends, with no public way to ask.
        """
        client = self.stagehand._rpc_client  # noqa: SLF001
        return client is not None and client._closed  # noqa: SLF001

    async def close(self) -> None:
        # Not `browser.close()`, which closes the browser. Closing Stagehand frees the
        # extension; dropping the CDP connection lets the browser idle out.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.stagehand.close(), 3)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(_invalidate_browser(self.stagehand.browser), 3)
        await asyncio.to_thread(shutil.rmtree, self.artifacts, ignore_errors=True)


# Process-wide: a user's concurrent threads share their browser's one client.
_SESSIONS: dict[str, _Session] = {}
# By browser key, for its client; by key and window, for the window's take, taken
# first. A take starts and ends in turn, and saving one holds up no other window.
_LOCKS: weakref.WeakValueDictionary[str | tuple[str, str], asyncio.Lock] = (
    weakref.WeakValueDictionary()
)
_RELEASES: dict[str, asyncio.Task[None]] = {}


@dataclass
class _Take:
    """A recording in progress, in the browser's sandbox: it outlives the client."""

    browser: str  # the browser's name, whose sandbox records
    folder: str  # where the recorder keeps the take, in that sandbox
    backend: BackendProtocol  # the sandbox the recording is saved to
    path: str | None  # where `start` said to save it
    box: str | None  # the browser sandbox's id: the take is lost with it


# By browser key and window: a user's threads record their own windows.
_TAKES: dict[tuple[str, str], _Take] = {}


class _StartReply(TypedDict):
    """The recorder's reply to ``start``."""

    recording: Literal[True]


class _StopReply(TypedDict):
    """The recorder's reply to ``stop``."""

    seconds: float
    early: str  # why it ended before it was stopped, as the recorder words it, or ""
    summary: str  # a line per event, as the recorder writes it


class _ErrorReply(TypedDict):
    """The recorder's reply when ``start`` or ``stop`` fails."""

    error: str


_START_REPLY = TypeAdapter(_StartReply)
_STOP_REPLY = TypeAdapter(_StopReply)
_ERROR_REPLY = TypeAdapter(_ErrorReply)


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


class BrowserMiddleware(AgentMiddleware[BrowserState, Any, Any]):
    """Give the agent Stagehand's tools, named ``browser_run``, ``browser_snapshot`` and so on,
    plus ``browser_handoff`` to hand the browser over and ``browser_record`` for a video.

    Without a sandbox, files save to ``backend``, the agent's own, where its file tools look.
    """

    name = "mda-browser"
    state_schema = BrowserState

    def __init__(
        self,
        *,
        scope: Scope = "thread",
        idle_ttl_seconds: int = 600,
        backend: BackendProtocol | None = None,
    ) -> None:
        super().__init__()
        self.scope: Scope = scope
        self.idle_ttl_seconds = idle_ttl_seconds
        self.backend = backend if backend is not None else StateBackend()
        self.tools = self._tools()
        self._arguments = {tool.name: set(tool.args) for tool in self.tools}
        self._instructions = f"{facade_instructions()}\n\n{_INSTRUCTIONS}"

    def browser_name(self, runtime: ManagedToolRuntime | Runtime) -> str:
        """The LangSmith browser this run's browser tools use, whether or not it exists yet.

        Neither creates nor opens it. This is the key's first browser: the tools move to
        a later one only when it is being deleted or broke.

        Raises:
            RuntimeError: the run has no thread, or user scope has no signed-in user.
        """
        key, _ = self._where(runtime)
        return _browser_name(key)

    async def _call(  # noqa: C901 - one flat chain of error handling
        self, runtime: ManagedToolRuntime, fn: Callable[[_Session], Awaitable[T]]
    ) -> tuple[T | str, str | None]:
        """Run ``fn`` on the run's session, and say which browser sandbox it ran on.
        Errors go to the model; nothing is retried.

        While the user has the window, the agent waits.
        """
        # The handoff is asked about here too: after a restart no watcher says it ended.
        if (pending := _pending(runtime.state)) and await _handoff_status(pending) is None:
            return f"error: {_WITH_USER}", None
        try:
            key, window = self._where(runtime)
        except RuntimeError as err:
            return f"error: {err}", None
        box = None
        async with _LOCKS.setdefault(key, asyncio.Lock()):
            try:
                if (session := _SESSIONS.get(key)) and (
                    session.window != window or session.disconnected
                ):
                    await _release(key)  # another of the user's threads, or Chrome restarted
                if key not in _SESSIONS:
                    _SESSIONS[key] = await _open(key, window, self.idle_ttl_seconds)
                await self._share(_SESSIONS[key], _SESSIONS[key].browser, key, runtime)
                box = _SESSIONS[key].box
                return await fn(_SESSIONS[key]), box
            except StagehandFacadeInputError as err:
                return f"error: {err}", box
            except StagehandFacadeExecutionError as err:  # thrown by run code
                return f"error: {err.name}: {err}", box
            except Exception as err:  # noqa: BLE001 - reported to the model
                # The connection may be dead, unless the browser answered or the facade
                # turned away run code that does not parse, with a plain SyntaxError.
                if not isinstance(err, RPCError | SyntaxError):
                    await _release(key)
                return f"error: {sanitize_error_message(f'{type(err).__name__}: {err}')}", box
            finally:
                if (session := _SESSIONS.get(key)) and session.closing:
                    await _release(key)
                if pending := _RELEASES.pop(key, None):
                    pending.cancel()
                _RELEASES[key] = asyncio.create_task(_release_when_idle(key))

    def _files(self, runtime: ManagedToolRuntime) -> BackendProtocol:
        """The run's sandbox, or the agent's own files."""
        return runtime.backend if runtime.backend is not None else self.backend

    def _where(self, runtime: ManagedToolRuntime | Runtime) -> tuple[str, str]:
        """The run's browser key and window; ``RuntimeError`` without a thread or user."""
        thread = _thread_id(runtime)
        key = _browser_key(runtime.server_info, thread, self.scope)
        return key, _window_name(key, thread)

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

    async def abefore_agent(self, state: BrowserState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        """A run the handoff's notification starts ends the handoff."""
        messages = state.get("messages") or []
        if _pending(state) and messages and _is_handoff_notification(messages[-1]):
            return {"mda_browser_handoff": None}
        return None

    async def aafter_agent(self, state: BrowserState, runtime: Runtime) -> None:  # noqa: ARG002
        """Save the run's recording and release the client; the browser and windows stay."""
        with contextlib.suppress(RuntimeError):  # no thread or user, so no session
            key, window = self._where(runtime)
            async with _LOCKS.setdefault((key, window), asyncio.Lock()):
                await _end_take(key, window)
            async with _LOCKS.setdefault(key, asyncio.Lock()):
                await _release(key)

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelResponse[object]:
        """Append the facade's instructions to the system message, as their own block."""
        system = request.system_message
        text = self._instructions
        # A user's browser serves several threads, whose sandboxes cannot all be localhost.
        if self.scope == "thread" and getattr(request.runtime, "backend", None) is not None:
            text += f" {_LOCALHOST_NOTE}"
        blocks = [*(system.content_blocks if system else []), create_text_block(text)]
        return await handler(request.override(system_message=SystemMessage(content_blocks=blocks)))

    async def _hand_to_user(
        self, runtime: ManagedToolRuntime, reason: str
    ) -> Command | list[str | dict[str, str]] | str:
        """Give the user the window and return at once, keeping the handoff in state.

        It needs no client, only the browsers API: a window a person controls turns a
        client away, so a second handoff could not connect. The agent's client lets go,
        and the next browser call connects afresh and takes the window back.
        """
        try:
            key, window = self._where(runtime)
        except RuntimeError as err:
            return f"error: {err}"
        async with _LOCKS.setdefault((key, window), asyncio.Lock()):  # no take starts meanwhile
            ended = await _end_take(key, window)  # saved before the user has the window
            async with _LOCKS.setdefault(key, asyncio.Lock()):
                await _release(key)
                try:
                    browser, window_id, url, box = await _hand_over(
                        key, window, self.idle_ttl_seconds
                    )
                except Exception as err:  # noqa: BLE001 - reported to the model
                    failed = f"error: {sanitize_error_message(f'{type(err).__name__}: {err}')}"
                    return _with_recording(failed, ended)
        until = time.time() + _HANDOFF_LINK_TTL_S
        pending: PendingBrowserHandoff = {
            "browser": browser,
            "window_id": window_id,
            "until": until,
        }
        try:
            await _start_watcher(runtime, pending)
        except Exception as err:  # noqa: BLE001 - its browser tools still work once it ended
            _LOG.warning("could not watch the handoff on thread %s: %s", _thread_id(runtime), err)
        # The link reaches the user through the artifact; the model never sees it.
        # What clients such as Studio show the user: the ask, and a link that lets
        # whoever holds it drive the window, for 15 minutes.
        handoff = {"type": "browser_handoff", "message": reason, "url": url}
        text, noted = _note_box(runtime.state, box, _with_recording(_HANDED_OVER, ended))
        reply = ToolMessage(
            text,
            artifact=handoff,
            name=f"{BROWSER_TOOL_PREFIX}handoff",
            tool_call_id=runtime.tool_call_id,
        )
        return Command(update={"mda_browser_handoff": pending, **noted, "messages": [reply]})

    async def _record(  # noqa: PLR0911 - one return per refusal
        self, runtime: ManagedToolRuntime, action: Literal["start", "stop"], path: str | None
    ) -> Command | list[dict[str, str]] | str:
        """Start or stop recording the run's window; a ``path`` that can't be saved does neither."""
        try:
            where = self._where(runtime)
        except RuntimeError as err:
            return f"error: {err}"
        if path is not None and not posixpath.isabs(path):
            if action == "start":
                return f"error: {path} is not an absolute path. Recording not started."
            return f"error: {path} not saved: give an absolute path. Still recording."
        async with _LOCKS.setdefault(where, asyncio.Lock()):
            if action == "start":
                if (backend := runtime.backend) is None:
                    return (
                        "error: browser_record needs a sandbox to save recordings in, "
                        "and this run has none. Screenshots still save."
                    )
                ran = await self._call(runtime, lambda s: _start_take(s, where, backend, path))
                return _reply(runtime, f"{BROWSER_TOOL_PREFIX}record", *ran)
            if (take := _TAKES.pop(where, None)) is None:
                return (
                    "error: not recording. A recording ends when the run ends or you "
                    "hand the browser to the user, and is saved where `start` said, "
                    f"or under {_RECORDINGS}/."
                )
            return await _finish_take(take, path or take.path)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Turn away argument names a browser tool lacks, such as snake_case ones.

        JSON-schema tools take their arguments unchecked, so an unknown name would reach
        the tool's function and fail the run.
        """
        call = request.tool_call
        accepted = self._arguments.get(call["name"])
        extra = [arg for arg in call["args"] if accepted is not None and arg not in accepted]
        if not extra:
            return await handler(request)
        return ToolMessage(_unexpected(extra), tool_call_id=call["id"], name=call["name"])

    def _tools(self) -> list[BaseTool]:
        """The facade's tools as its hosts advertise them, names prefixed ``browser_``."""
        facade = {entry["name"]: entry for entry in facade_tools()}
        path = {"type": "string", "description": _SCREENSHOT_PATH}
        facade["screenshot"]["inputSchema"]["properties"]["path"] = path

        def advertised(name: str) -> Callable[[Callable[..., Awaitable[object]]], BaseTool]:
            entry = facade[name]
            return tool(
                f"{BROWSER_TOOL_PREFIX}{name}",
                description=entry["description"],
                args_schema=entry["inputSchema"],
            )

        @advertised("run")
        async def run(
            runtime: ManagedToolRuntime,
            code: str | None = None,
            actions: list[dict[str, Any]] | None = None,
        ) -> Command | str:
            async def go(session: _Session) -> str:
                try:
                    value = await session.tools.run(code=code, actions=actions)
                finally:  # code that threw may have saved some first
                    saved = await _save_artifacts(self._files(runtime), Path(session.artifacts))
                return "\n".join([json.dumps(value, default=str), *saved])

            return _reply(runtime, f"{BROWSER_TOOL_PREFIX}run", *await self._call(runtime, go))

        @advertised("snapshot")
        async def snapshot(
            runtime: ManagedToolRuntime,
            includeIframes: bool = True,  # noqa: N803 - the facade's name
        ) -> Command | str:
            ran = await self._call(
                runtime, lambda s: s.tools.snapshot(include_iframes=includeIframes)
            )
            return _reply(runtime, f"{BROWSER_TOOL_PREFIX}snapshot", *ran)

        @advertised("screenshot")
        async def screenshot(
            runtime: ManagedToolRuntime,
            fullPage: bool | None = None,  # noqa: N803 - the facade's name
            type: Literal["png", "jpeg"] = "png",
            quality: float | None = None,
            path: str | None = None,
        ) -> Command | list[dict[str, str]] | str:
            shot, box = await self._call(
                runtime,
                lambda s: s.tools.screenshot(full_page=fullPage, type=type, quality=quality),
            )
            if isinstance(shot, str):
                return _reply(runtime, f"{BROWSER_TOOL_PREFIX}screenshot", shot, box)
            text = "Screenshot of the current page."
            if path:
                data = base64.b64decode(shot["data"])
                text += " " + await _save(self._files(runtime), path, data)
            image = {"type": "image", "url": f"data:{shot['mimeType']};base64,{shot['data']}"}
            return _reply(
                runtime, "browser_screenshot", [{"type": "text", "text": text}, image], box
            )

        @tool
        async def browser_handoff(
            runtime: ManagedToolRuntime, reason: str
        ) -> Command | list[str | dict[str, str]] | str:
            """Hand the browser to the user; you'll be notified when they hand it back.

            Use it for what only they should do: log in, solve a CAPTCHA, approve a
            2FA prompt. Never ask for passwords. `reason` is shown to them as is: a
            few words on what to do, such as "Log in to LinkedIn".
            """
            return await self._hand_to_user(runtime, reason)

        @tool(f"{BROWSER_TOOL_PREFIX}record", description=_RECORD_DESCRIPTION)
        async def record(
            runtime: ManagedToolRuntime,
            action: Literal["start", "stop"],
            path: str | None = None,
        ) -> Command | list[dict[str, str]] | str:
            return await self._record(runtime, action, path)

        return [run, snapshot, screenshot, browser_handoff, record]


def _pending(state: BrowserState | Mapping[str, Any] | None) -> PendingBrowserHandoff | None:
    return state.get("mda_browser_handoff") if isinstance(state, Mapping) else None


def _note_box(
    state: object, box: str | None, content: str | list[Any]
) -> tuple[str | list[Any], dict[str, str]]:
    """What a call on browser sandbox ``box`` returns, and the update that notes ``box``.

    A box the thread hasn't used is noted; if it used another, it was deleted, and the
    model reads so first.
    """
    used = state.get("mda_browser") if isinstance(state, Mapping) else None
    if box is None or box == used:
        return content, {}
    if used is not None:
        replaced = {"type": "text", "text": _REPLACED}
        content = f"{_REPLACED}\n{content}" if isinstance(content, str) else [replaced, *content]
    return content, {"mda_browser": box}


def _reply(
    runtime: ManagedToolRuntime, name: str, content: _Content, box: str | None
) -> Command | _Content:
    """Tool ``name``'s reply to a call on browser sandbox ``box``, noting ``box`` in state
    if new."""
    said, noted = _note_box(runtime.state, box, content)
    if not noted:
        return content
    message = ToolMessage(said, name=name, tool_call_id=runtime.tool_call_id)
    return Command(update={**noted, "messages": [message]})


async def _start_watcher(runtime: ManagedToolRuntime, pending: PendingBrowserHandoff) -> None:
    """Start a run of the watcher graph on a thread of its own, as deepagents starts
    an async subagent's task: the thread's metadata names this agent, which the Agent
    Server copies into the run, the run gets this run's context, and its trace nests
    under this tool call (``parent_run`` in the generated entry adopts it).

    The wait is a run rather than a task in this process, so the wake-up comes from a
    live run, as in TypeScript, where only a live run can reach the Agent Server.
    """
    reference = _parent_reference(runtime.config)
    handoff: dict[str, Any] = {**pending}
    client = get_client()
    thread = await client.threads.create(
        metadata={f"parent_{key}": value for key, value in reference.items()}
    )
    await client.runs.create(
        thread["thread_id"],
        HANDOFF_WATCHER_GRAPH,
        input={"handoff": handoff},
        context=_forwarded_context(runtime.context),
        # A subagent on this deployment (no `url`) with no headers of its own.
        headers=_trace_headers(cast("AsyncSubAgent", {})),
    )


@dataclass
class _WatcherState:
    handoff: PendingBrowserHandoff


async def _watch(state: _WatcherState, runtime: Runtime) -> dict[str, Any]:
    """Once the handoff ends, notify the agent that started it, unless it has moved on:
    a newer handoff replaced this one, or the agent waits on a person, where a new run
    would drop what it waits for. Its browser tools work again either way.
    """
    pending = state.handoff
    status = None
    while status is None:
        await asyncio.sleep(_HAND_BACK_POLL_S)
        status = await _handoff_status(pending)
    parent = _launching_agent(get_config()) or {}
    if "thread_id" not in parent or "assistant_id" not in parent:
        return {}
    client = get_client()
    thread = await client.threads.get(parent["thread_id"])
    if thread["status"] == "interrupted" or _pending(thread["values"]) != pending:
        return {}
    await client.runs.create(
        parent["thread_id"],
        parent["assistant_id"],
        input={"messages": [_wake_message(status)]},
        multitask_strategy="enqueue",  # after a run that is going on
        context=_forwarded_context(runtime.context),
    )
    return {}


#: The watcher graph: one run per handoff, from ``browser_handoff`` until it ends.
handoff_watcher = (
    StateGraph(_WatcherState).add_node("watch", _watch).add_edge(START, "watch").compile()
)


async def _handoff_status(pending: PendingBrowserHandoff) -> BrowserHandoffStatus | None:
    """How the handoff ended, or ``None`` while the user still has the window."""
    url, headers = _api()
    with contextlib.suppress(httpx.HTTPError, ValueError, KeyError):  # asked again later
        async with httpx.AsyncClient(headers=headers, timeout=10) as api:
            browser = await api.get(f"{url}/browsers/{pending['browser']}")
        if browser.status_code == httpx.codes.NOT_FOUND:
            return "ended"
        windows = browser.raise_for_status().json().get("attachments") or []
        window = next((w for w in windows if w["id"] == pending["window_id"]), None)
        if window is None:
            return "ended"
        if window.get("controller") == "agent":
            return "handed_back"
    return "ended" if time.time() >= pending["until"] else None


def _wake_message(status: BrowserHandoffStatus) -> dict[str, Any]:
    """A notification in deepagents' task notification's shape: a user-role message
    framed as a system notification. Its ``mda_notification`` tells clients how the
    handoff ended, so they can tell it from the user's message."""
    return {
        "role": "user",
        "content": f"[SYSTEM NOTIFICATION - NOT USER INPUT]\n{_WAKE_TEXT[status]}",
        MDA_NOTIFICATION_KEY: {"type": "browser_handoff", "status": status},
    }


def _is_handoff_notification(message: BaseMessage) -> bool:
    notification = message.additional_kwargs.get(MDA_NOTIFICATION_KEY)
    return isinstance(notification, dict) and notification.get("type") == "browser_handoff"


async def _end_take(key: str, window: str) -> list[dict[str, str]] | str | None:
    """Save the window's recording, if any, where `start` said or under recordings/.

    Call it under the window's lock. Returns what `_finish_take` does, or None if
    nothing was recording.
    """
    if (take := _TAKES.pop((key, window), None)) is None:
        return None
    try:
        result = await _finish_take(take, take.path)
    except Exception as err:  # noqa: BLE001 - the run ends, or the user gets the window, regardless
        result = f"error: {type(err).__name__}: {err}"
    if isinstance(result, str):
        _LOG.warning("could not save %s's recording: %s", key, sanitize_error_message(result))
    return result


def _with_recording(
    text: str, ended: list[dict[str, str]] | str | None
) -> list[str | dict[str, str]] | str:
    """``text``, then what became of the recording a handoff ended, if one was running."""
    if ended is None:
        return text
    if isinstance(ended, str):
        return f"{text}\nYour recording ended here: {ended}"
    note, *frames = ended
    return [{"type": "text", "text": f"{text}\nYour recording ended here. {note['text']}"}, *frames]


async def _finish_take(take: _Take, path: str | None) -> list[dict[str, str]] | str:
    """Stop ``take`` and save it to ``path``, or under recordings/ if it can't be saved there.

    Returns what was saved and the first and last frames, for the model to check.
    """
    try:
        stopped = await _stop_take(take)
    except Exception as err:  # noqa: BLE001 - reported to the model
        if _deleted(err):
            return _LOST
        return f"error: {sanitize_error_message(f'{type(err).__name__}: {err}')}"
    if stopped is None:
        return _LOST
    reply, (video, timeline, first, last) = stopped
    default = f"{_RECORDINGS}/{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    path = path or default
    saved = await _save(take.backend, path, video)
    if saved.startswith("error:"):
        # The browser's sandbox no longer has the take, so try once more, under recordings/.
        missed = sanitize_error_message(saved.removeprefix("error: "))
        moved = await _save(take.backend, default, video)
        if moved.startswith("error:"):
            return f"error: {missed}. The recording is lost."
        saved = moved if path == default else f"{missed}. {moved[:-1]} instead."
        path = default
    beside = f"{posixpath.splitext(_normpath(path))[0]}.json"
    noted = await _save(take.backend, beside, timeline)
    text = f"{saved[:-1]}: {reply['seconds']:.0f}s, {len(video) / 1e6:.1f} MB. {noted}"
    if reply["early"]:
        text += f" {reply['early']}"
    text += f"\nWhat happened, at video time:\n{reply['summary']}"
    text += "\nIts first and last frames follow; check them before sharing it."
    return [
        {"type": "text", "text": text},
        *(
            {"type": "image", "url": f"data:image/jpeg;base64,{base64.b64encode(f).decode()}"}
            for f in (first, last)
        ),
    ]


def _unexpected(arguments: list[str]) -> str:
    names = ", ".join(repr(name) for name in arguments)
    return f"error: unexpected argument{'s' if len(arguments) > 1 else ''} {names}."


async def _save_artifacts(backend: BackendProtocol, root: Path) -> list[str]:
    """Move what the facade saved under ``root`` to the same relative path in /workspace."""
    files = await asyncio.to_thread(_take_files, root)
    return [await _save(backend, f"{_WORKSPACE}/{rel}", data) for rel, data in files]


def _take_files(root: Path) -> list[tuple[str, bytes]]:
    files = [
        (p.relative_to(root).as_posix(), p.read_bytes()) for p in root.rglob("*") if p.is_file()
    ]
    for rel, _ in files:
        (root / rel).unlink()
    return sorted(files)


async def _save(backend: BackendProtocol, path: str, data: bytes) -> str:
    """Write ``data`` to absolute ``path`` in ``backend``, creating its folder."""
    if not posixpath.isabs(path):
        return f"error: {path} not saved: give an absolute path."
    path = _normpath(path)
    if mount := _mount(backend, path):
        return (
            f"error: {path} not saved: {mount} is not for saved files. Save it under {_WORKSPACE}."
        )
    sandbox = isinstance(backend, SandboxBackendProtocol)
    if not sandbox and len(data) > _MAX_SAVED_BYTES:
        mb = math.ceil(len(data) / 1e5) / 10
        return (
            f"error: {path} not saved: {mb:.1f} MB is over the 5 MB limit for files saved "
            "without a sandbox. Take a viewport screenshot instead of a full page."
        )
    try:
        if sandbox:
            await backend.aexecute(f"mkdir -p -- {shlex.quote(posixpath.dirname(path))}")
        [result] = await backend.aupload_files([(path, data)])
    except Exception as err:  # noqa: BLE001 - reported to the model
        return f"error: {path} not saved: {type(err).__name__}: {err}"
    return f"error: {path} not saved: {result.error}" if result.error else f"Saved {path}."


def _normpath(path: str) -> str:
    """Absolute ``path`` with one leading slash, as Node normalizes it; POSIX keeps two."""
    return "/" + posixpath.normpath(path).lstrip("/")


def _mount(backend: BackendProtocol, path: str) -> str | None:
    """The Context Hub mount ``path`` is in, such as ``/skills``: read-only, or read by every
    later run."""
    if not isinstance(backend, CompositeBackend):
        return None
    mounts = (prefix.rstrip("/") for prefix, _ in backend.sorted_routes)
    return next((mount for mount in mounts if f"{path}/".startswith(f"{mount}/")), None)


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


async def _embed_origins(api: httpx.AsyncClient) -> list[str]:
    """Origins whose pages may frame a handoff's window: the LangSmith UI, plus any in
    ``MDA_BROWSER_EMBED_ORIGINS`` (comma-separated), such as a UI preview build."""
    extra = (os.environ.get("MDA_BROWSER_EMBED_ORIGINS") or "").split(",")
    ui = await _langsmith_ui_origin(api)
    return list(dict.fromkeys([ui, *filter(None, map(str.strip, extra))]))


async def _langsmith_ui_origin(api: httpx.AsyncClient) -> str:
    """The LangSmith UI's origin, where Studio may embed a handoff's window: the
    workspace's ``dashboard_url``."""
    info = await api.get(f"{_endpoint()}/v1/platform/workspaces/current/info")
    dashboard = httpx.URL(info.json()["dashboard_url"])
    return f"{dashboard.scheme}://{dashboard.netloc.decode()}"


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
        await _create(api, browsers, name, idle_ttl_seconds)
        try:
            box = await sandboxes.get_sandbox(name)
        except ResourceNotFoundError:  # it finished deleting since: its name is free
            await _create(api, browsers, name, idle_ttl_seconds)
            box = await sandboxes.get_sandbox(name)
        if box.status in _UNUSABLE:
            continue
        if box.status == "stopped":
            box = await sandboxes.start_sandbox(name, timeout=300) or box
        return name, box
    msg = "could not find or create a usable browser"
    raise RuntimeError(msg)


async def _create(api: httpx.AsyncClient, browsers: str, name: str, idle_ttl_seconds: int) -> None:
    """Create browser ``name``, unless it exists."""
    try:
        # HiDPI renders at twice the resolution, so a person watching or driving the
        # window sees it sharp; it can only be chosen at creation.
        create = {"name": name, "idle_ttl_seconds": idle_ttl_seconds, "hidpi": True}
        await api.post(browsers, json=create)
    except httpx.HTTPStatusError as err:
        if err.response.status_code != httpx.codes.CONFLICT:  # conflict: it exists
            raise


# --- LangSmith: create the browser, put Stagehand's extension on it, connect -------

_BROWSER_GENERATIONS = 5
_UNUSABLE = {"deleting", "deleted", "failed", "error"}  # browser states to move past
_EXTENSION_DIR = f"/var/lib/browserd/extensions/stagehand-{STAGEHAND_VERSION}"
_EXTENSION_ZIP = "/tmp/mda-stagehand.zip"  # noqa: S108 - in the browser sandbox
_WINDOWS_FILE = "/var/lib/browserd/attachments.json"  # browserd's windows and their ports
_LOCALHOST_WINDOW = "mda-localhost"  # shares a sandbox's localhost with every window
# The recorder runs in the browser's sandbox with these, installed beside it; the
# package's tests run it with the same versions.
_RECORDER_PACKAGES = ("av==19.0.2", "pillow==13.0.0", "websockets==16.1.1")
_RECORDER_HOME = "/var/lib/mda-recorder"  # in the browser sandbox: its packages
_RECORDER_PACKAGES_DIR = (
    f"{_RECORDER_HOME}/packages-"
    + hashlib.sha256(" ".join(_RECORDER_PACKAGES).encode()).hexdigest()[:16]
)
# In the browser sandbox: a take's folder, `<prefix>-<id>`, which holds the take and its
# own copy of the recorder. Python looks for modules beside the program first, so the
# folder holds no other.
_RECORDER_TAKES = "/tmp/mda-recording"  # noqa: S108 - in the browser sandbox
_RECORDER_PYTHON = "python3"
_RECORDER_STOP_TIMEOUT_S = 660  # the recorder's own 600 s to encode, and some


def _api() -> tuple[str, dict[str, str]]:
    """LangSmith's sandboxes API, and its headers: the key, and the workspace if set."""
    tenant = {"X-Tenant-Id": ws} if (ws := os.environ.get("LANGSMITH_WORKSPACE_ID")) else {}
    return f"{_endpoint()}/v2/sandboxes", {"X-Api-Key": get_env_var("API_KEY") or "", **tenant}


def _endpoint() -> str:
    """LangSmith's API."""
    return (get_env_var("ENDPOINT") or "https://api.smith.langchain.com").rstrip("/")


async def _open(key: str, window_name: str, idle_ttl_seconds: int) -> _Session:
    """Attach Stagehand to a window of ``key``'s browser, creating either as needed.

    The client reaches Chrome through the API's CDP proxy; the extension, inside the
    browser sandbox, dials the window's local port. A window the user still holds
    from an ended handoff is taken back.
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
        window = await _window(api, f"{browsers}/{name}", window_name)
        if window.get("controller") == "user":
            controller = f"{browsers}/{name}/attachments/{window['id']}/controller"
            await api.post(controller, json={"controller": "agent"})
        cdp_url = (await api.get(f"{window['api_cdp_url']}/json/version")).json()[
            "webSocketDebuggerUrl"
        ]
        port = await _window_port(box, window["id"])

    local_url = f"ws://127.0.0.1:{port}/devtools/browser/{window['id']}"
    try:
        return await _connect(key, window_name, name, cdp_url, local_url, headers, box=box.id)
    except Exception as err:
        if "already initialized" not in str(err):
            raise
    # A client that never closed (a crash, another replica) left its claim in the
    # extension; reloading it clears the claim without restarting Chrome.
    # Known limit: two live clients of one browser (two replicas, user scope) take it from
    # each other; the loser fails a call (~13 s RPC timeout) and reopens, never on the wrong window.
    await _reload_extension(cdp_url, headers)
    return await _connect(key, window_name, name, cdp_url, local_url, headers, box=box.id)


async def _window(api: httpx.AsyncClient, browser: str, name: str) -> dict[str, Any]:
    """The browser's window ``name``, created for the agent if it has none."""
    windows = (await api.get(browser)).json().get("attachments") or []
    if window := next((w for w in windows if w["name"] == name), None):
        return window
    body = {"name": name, "controller": "agent"}
    return (await api.post(f"{browser}/attachments", json=body)).json()


async def _hand_over(
    key: str, window_name: str, idle_ttl_seconds: int
) -> tuple[str, str, str, str | None]:
    """Give the user ``key``'s window, creating either as needed.

    Returns the browser's name, the window's id, a short-lived link the user drives it
    with, and the browser sandbox's id.
    """
    url, headers = _api()
    browsers = f"{url}/browsers"
    async with (
        httpx.AsyncClient(headers=headers, timeout=90, event_hooks={"response": [_raise]}) as api,
        AsyncSandboxClient(api_key=headers["X-Api-Key"], headers=headers) as sandboxes,
    ):
        name, box = await _usable_browser(api, sandboxes, browsers, key, idle_ttl_seconds)
        window = await _window(api, f"{browsers}/{name}", window_name)
        window_api = f"{browsers}/{name}/attachments/{window['id']}"
        await api.post(f"{window_api}/controller", json={"controller": "user"})
        body = {"ttl_seconds": _HANDOFF_LINK_TTL_S, "embed_origins": await _embed_origins(api)}
        link = (await api.post(f"{window_api}/links", json=body)).json()["act_url"]
    return name, window["id"], link, box.id


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
    key: str,
    window: str,
    browser_name: str,
    cdp_url: str,
    local_url: str,
    headers: dict[str, str],
    *,
    box: str | None = None,
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
    # File work runs off the event loop: `langgraph dev` turns blocking calls on it away.
    artifacts = await asyncio.to_thread(lambda: tempfile.mkdtemp(prefix="mda-browser-"))
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
    return _Session(window, stagehand, tools, artifacts, browser_name, box, local_url=local_url)


async def _newest_tab(context: BrowserContext) -> Page:
    """The window's newest tab, as it was: the client's CDP lists only its window's."""
    pages = await context.pages()
    return pages[-1] if pages else await context.new_page()


async def _install_extension(box: AsyncSandbox) -> None:
    """Unpack the client's own extension into the browser, unless it is there already."""
    if (await box.run(f"test -f {_EXTENSION_DIR}/manifest.json", timeout=60)).exit_code == 0:
        return
    archive = await asyncio.to_thread(get_extension_archive)  # zips its folder: blocking
    await box.write(_EXTENSION_ZIP, archive, timeout=300)
    tmp = f"{_EXTENSION_DIR}.tmp"  # unpacked aside, so a partial copy never passes the check
    unpacked = await box.run(
        f"rm -rf {tmp} && python3 -m zipfile -e {_EXTENSION_ZIP} {tmp} && "
        f"rm -rf {_EXTENSION_DIR} && mv {tmp} {_EXTENSION_DIR}",
        timeout=300,
    )
    if unpacked.exit_code != 0:
        msg = f"could not install Stagehand in the browser: {unpacked.stderr[-500:]}"
        raise RuntimeError(msg)


async def _start_take(
    session: _Session, where: tuple[str, str], backend: BackendProtocol, path: str | None
) -> str:
    """Record the newest tab of the session's window, beside Chrome in its sandbox.

    The recorder follows newer tabs itself, and records until it is stopped or reaches
    its limit, whatever happens to this process's client.
    """
    if where in _TAKES:
        return "error: already recording. Stop this recording first."
    tab = await _newest_tab(session.stagehand.browser.context)
    folder = f"{_RECORDER_TAKES}-{uuid.uuid4()}"
    start = _recorder_command(folder, "start", session.local_url, tab.page_id)
    async with _browser_box(session.browser) as box:
        try:
            await box.run(f"mkdir -p -- {shlex.quote(folder)}", timeout=60)
            source = await asyncio.to_thread(_recorder_source)
            await box.write(f"{folder}/recorder.py", source, timeout=60)
            await _recorder(box, f"{_install_recorder()} && {start}", 300, _START_REPLY)
        except RuntimeError as err:  # the recorder's own; the client is fine
            await _remove_take(box, folder)
            return f"error: {sanitize_error_message(str(err))}"
    _TAKES[where] = _Take(session.browser, folder, backend, path, box.id)
    width, height = _RECORD_VIEWPORT
    return (
        f"Recording. The viewport is {width}x{height} until you stop, "
        "so take a browser_snapshot before acting on the page."
    )


async def _stop_take(take: _Take) -> tuple[_StopReply, list[bytes]] | None:
    """Stop ``take``, wait while the recorder encodes it, and download it.

    Returns the recorder's reply, then the video, its timeline, and its first and last
    frames; None if its browser was deleted and made again, without the take.
    """
    stop = _recorder_command(take.folder, "stop")
    async with _browser_box(take.browser) as box:
        if box.id != take.box:
            return None
        try:
            reply = await _recorder(box, stop, _RECORDER_STOP_TIMEOUT_S, _STOP_REPLY)
            files = [
                await box.read(f"{take.folder}/{name}", timeout=300)
                for name in ("take.mp4", "take.json", "first.jpg", "last.jpg")
            ]
        finally:
            await _remove_take(box, take.folder)  # the take holds what the tab showed
    return reply, files


def _deleted(err: Exception) -> bool:
    """Whether ``err`` says the browser's sandbox is gone."""
    return isinstance(err, ResourceNotFoundError) and err.resource_type == "sandbox"


async def _remove_take(box: AsyncSandbox, folder: str) -> None:
    """Remove a take's folder, with its copy of the recorder; it never fails."""
    with contextlib.suppress(Exception):
        await box.run(f"rm -rf -- {shlex.quote(folder)}", timeout=60)


@contextlib.asynccontextmanager
async def _browser_box(name: str) -> AsyncGenerator[AsyncSandbox]:
    """Browser ``name``'s sandbox, where the recorder runs."""
    _, headers = _api()
    async with AsyncSandboxClient(api_key=headers["X-Api-Key"], headers=headers) as sandboxes:
        yield await sandboxes.get_sandbox(name)


async def _recorder(box: AsyncSandbox, command: str, timeout: int, reply: TypeAdapter[T]) -> T:  # noqa: ASYNC109 - the sandbox's own timeout
    """Run a recorder ``command``; its ``reply``, or ``RuntimeError`` with why it failed."""
    ran = await box.run(command, timeout=timeout)
    last = (ran.stdout.strip().splitlines() or [""])[-1]
    if ran.exit_code == 0:
        with contextlib.suppress(ValidationError):
            return reply.validate_json(last, strict=True)
    why = ""
    with contextlib.suppress(ValidationError):
        why = _ERROR_REPLY.validate_json(last, strict=True)["error"]
    msg = f"the recorder failed: {why or ran.stderr.strip()[-300:] or f'exit {ran.exit_code}'}"
    raise RuntimeError(msg)


def _recorder_source() -> bytes:
    return Path(__file__).with_name("_browser_recording.py").read_bytes()


def _install_recorder() -> str:
    """A command that installs the recorder's packages, unless they are there.

    Installed aside, so a partial install never passes the check.
    """
    target, home = shlex.quote(_RECORDER_PACKAGES_DIR), shlex.quote(_RECORDER_HOME)
    pins = " ".join(shlex.quote(pin) for pin in _RECORDER_PACKAGES)
    pip = (
        f"{_RECORDER_PYTHON} -m pip install --quiet --disable-pip-version-check --no-cache-dir "
        f'--root-user-action=ignore --only-binary=:all: --target "$tmp" {pins}'
    )
    return (
        f"{{ test -e {target}/.installed || {{ mkdir -p {home} && "
        f"tmp=$(mktemp -d {home}/install.XXXXXX) && "
        f'{{ {pip} >"$tmp.log" 2>&1 || {{ tail -c 300 "$tmp.log" >&2; exit 1; }}; }} && '
        f'rm -f "$tmp.log" && touch "$tmp/.installed" && '
        f'{{ mv -T "$tmp" {target} 2>/dev/null || rm -rf "$tmp"; }}; }}; }}'
    )


def _recorder_command(folder: str, action: str, *args: str) -> str:
    """Run the take's own copy of the recorder, in ``folder``, on ``folder``."""
    words = " ".join(shlex.quote(arg) for arg in (f"{folder}/recorder.py", action, folder, *args))
    return f"PYTHONPATH={shlex.quote(_RECORDER_PACKAGES_DIR)} {_RECORDER_PYTHON} {words}"


async def _reload_extension(cdp_url: str, headers: dict[str, str]) -> None:
    """Load Stagehand's extension again over CDP, which reloads it in place.

    Not uninstalled first: Chrome uninstalls over CDP only an extension loaded over
    CDP, and browserd loads it otherwise when Chrome starts.
    """
    async with asyncio.timeout(30), websockets.connect(cdp_url, additional_headers=headers) as ws:
        await ws.send(
            json.dumps(
                {"id": 0, "method": "Extensions.loadUnpacked", "params": {"path": _EXTENSION_DIR}}
            )
        )
        while (reply := json.loads(await ws.recv())).get("id") != 0:
            pass  # events share the socket
        if "error" in reply:
            msg = f"CDP Extensions.loadUnpacked failed: {reply['error']}"
            raise RuntimeError(msg)


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
