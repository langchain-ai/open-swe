"""Browser use: Stagehand's Python facade tools on a LangSmith browser.

A browser per key (``thread:<id>``, or ``user:<id>`` under user scope). A thread's
own browser has one window, ``mda``; a user's has a window per thread, so their
threads share logins but not tabs. Each
browser has one Stagehand client in this process (the extension serves one at a
time), released between runs and after a minute idle so the browser can idle out.

``browser_handoff`` hands the window to the user and returns at once. When the user
hands it back, MDA enqueues a run on the thread with a notification, as deepagents
tells an agent its async task finished.

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
import time
import uuid
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, NotRequired, TypedDict, TypeVar, cast

import httpx
import websockets
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.middleware.async_subagents import _forwarded_context
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, OmitFromInput
from langchain.tools import BaseTool, tool
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.messages.content import create_text_block
from langgraph.types import Command
from langgraph_sdk import get_client
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

from managed_deepagents import _browser_recording as recording
from managed_deepagents._channels.assistant_text import MDA_NOTIFICATION_KEY
from managed_deepagents._managed_tools import ManagedToolRuntime  # noqa: TC001 - tool annotations
from managed_deepagents.identity import ManagedServerInfo
from managed_deepagents.runtime import (
    _LazyManagedSandboxBackend,
    _managed_deployment_name,
    _managed_sandbox_name,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
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
_RECORDINGS = f"{_WORKSPACE}/recordings"
_RECORD_DESCRIPTION = f"""Record the browser tab as a video, to show a person what you did.

`start`, act as usual, then `stop`: it saves an H.264 mp4 to `path` in your sandbox
(default {_RECORDINGS}/<time>.mp4), with what happened when beside it as JSON (the same
path, ending .json), and shows you its first and last frames. The video shows a cursor
gliding to each click, the clicks, and what is typed (masked in password fields). While
recording, the viewport is {recording.VIEWPORT[0]}x{recording.VIEWPORT[1]}, and each
pause between your calls plays for at most {recording.MAX_HOLD_S:g}s.
A recording ends, and is saved under {_RECORDINGS}/, when the run ends or you hand
the browser to the user: it never shows what they do. It stops at
{recording.MAX_S // 60} minutes."""
_HANDOFF_LINK_TTL_S = 900  # how long the link a user gets for a handoff works
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
_LOG = logging.getLogger("mda.browser")


BrowserHandoffStatus = Literal["handed_back", "ended"]
"""handed_back when the user hands the window back; ended when the handoff's link ran
out, or its browser or window is gone."""


class BrowserHandoff(TypedDict):
    """The ``browser_handoff`` tool's artifact: what a client shows the user.

    The tool returns at once; the model reads only the tool's text, never this.
    """

    type: Literal["browser_handoff"]
    """Tells clients how to read this artifact."""

    message: str
    """What the agent asked the user to do."""

    url: str
    """A link that lets whoever holds it drive the window, for 15 minutes."""


class BrowserHandoffNotification(TypedDict):
    """Marks the message that tells the agent a handoff ended, under
    ``additional_kwargs["mda_notification"]``, so clients can tell it from the user's."""

    type: Literal["browser_handoff"]
    """Tells clients how to read this notification."""

    status: BrowserHandoffStatus
    """How the handoff ended."""


class BrowserHandoffWake(TypedDict):
    """The message that tells the agent a handoff ended, in deepagents' task
    notification's shape: a user-role message framed as a system notification."""

    role: Literal["user"]
    content: str
    mda_notification: BrowserHandoffNotification


class PendingBrowserHandoff(TypedDict):
    """A handoff that waits on the user, in the thread's state."""

    browser: str
    """The LangSmith browser's name."""

    window_id: str
    """The window the user has."""

    until: float
    """When the user's link runs out, in epoch seconds."""


class BrowserState(AgentState):
    """The thread's handoff that waits on the user, as deepagents keeps its async tasks."""

    mda_browser_handoff: Annotated[NotRequired[PendingBrowserHandoff | None], OmitFromInput]


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
    cdp_url: str = ""  # this window's CDP, through the browsers API
    take: _Take | None = None  # a recording in progress

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
_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_RELEASES: dict[str, asyncio.Task[None]] = {}
_WATCHERS: set[asyncio.Task[None]] = set()  # held until done: the loop keeps weak references


@dataclass
class _Take:
    recorder: recording.Recorder
    runtime: ManagedToolRuntime  # whose sandbox the recording is saved to


async def _release(key: str) -> None:
    if session := _SESSIONS.pop(key, None):
        with contextlib.suppress(Exception):  # best effort: the client goes regardless
            await _finish_recording(session, None)
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
    plus ``browser_handoff`` to hand the browser over and ``browser_record`` for a video."""

    name = "mda-browser"
    state_schema = BrowserState

    def __init__(self, *, scope: Scope = "thread", idle_ttl_seconds: int = 600) -> None:
        super().__init__()
        self.scope: Scope = scope
        self.idle_ttl_seconds = idle_ttl_seconds
        self.tools = self._tools()
        self._instructions = f"{facade_instructions()}\n\n{_INSTRUCTIONS}"
        if scope == "thread":
            self._instructions += f" {_LOCALHOST_NOTE}"

    def browser_name(self, runtime: ManagedToolRuntime | Runtime) -> str:
        """The LangSmith browser this run uses, whether or not it exists yet."""
        return _browser_name(_browser_key(runtime.server_info, _thread_id(runtime), self.scope))

    async def _call(  # noqa: C901 - one flat chain of error handling
        self, runtime: ManagedToolRuntime, fn: Callable[[_Session], Awaitable[T]]
    ) -> T | str:
        """Run ``fn`` on the run's session. Errors go to the model; nothing is retried.

        While the user has the window, the agent waits.
        """
        # The handoff is asked about here too: after a restart no watcher says it ended.
        if (pending := _pending(runtime.state)) and await _handoff_status(pending) is None:
            return f"error: {_WITH_USER}"
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
                result = await fn(_SESSIONS[key])
                await _follow_tab(key)
                return result
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

    async def abefore_agent(self, state: BrowserState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        """A run the handoff's notification starts ends the handoff."""
        messages = state.get("messages") or []
        if _pending(state) and messages and _is_handoff_notification(messages[-1]):
            return {"mda_browser_handoff": None}
        return None

    async def aafter_agent(self, state: BrowserState, runtime: Runtime) -> None:  # noqa: ARG002
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

    async def _hand_to_user(self, runtime: ManagedToolRuntime, reason: str) -> Command | str:
        """Give the user the window and return at once, keeping the handoff in state.

        It needs no client, only the browsers API: a window a person controls turns a
        client away, so a second handoff could not connect. The agent's client lets go,
        and the next browser call connects afresh and takes the window back.
        """
        try:
            thread = _thread_id(runtime)
            key = _browser_key(runtime.server_info, thread, self.scope)
        except RuntimeError as err:
            return f"error: {err}"
        async with _LOCKS.setdefault(key, asyncio.Lock()):
            await _release(key)
            try:
                browser, window_id, url = await _hand_over(
                    key, _window_name(key, thread), self.idle_ttl_seconds
                )
            except Exception as err:  # noqa: BLE001 - reported to the model
                return f"error: {sanitize_error_message(f'{type(err).__name__}: {err}')}"
        until = time.time() + _HANDOFF_LINK_TTL_S
        pending: PendingBrowserHandoff = {
            "browser": browser,
            "window_id": window_id,
            "until": until,
        }
        watcher = asyncio.create_task(self._wake_on_hand_back(runtime, pending))
        _WATCHERS.add(watcher)
        watcher.add_done_callback(_WATCHERS.discard)
        # The link reaches the user through the artifact; the model never sees it.
        handoff: BrowserHandoff = {"type": "browser_handoff", "message": reason, "url": url}
        reply = ToolMessage(
            _HANDED_OVER,
            artifact=handoff,
            name="browser_handoff",
            tool_call_id=runtime.tool_call_id,
        )
        return Command(update={"mda_browser_handoff": pending, "messages": [reply]})

    async def _wake_on_hand_back(
        self, runtime: ManagedToolRuntime, pending: PendingBrowserHandoff
    ) -> None:
        """Once the handoff ends, notify the thread, unless a newer handoff replaced it.

        The watcher lives in this process. After a restart nothing notifies the
        thread; its browser tools still work once the handoff has ended.
        """
        status = None
        while status is None:
            await asyncio.sleep(_HAND_BACK_POLL_S)
            status = await _handoff_status(pending)
        thread_id = _thread_id(runtime)
        try:
            thread = await get_client().threads.get(thread_id)
            # As deepagents' parent notifications: a new run on a thread that waits on
            # a person would drop what it waits for. Its browser tools work again.
            if thread["status"] != "interrupted" and _pending(thread["values"]) == pending:
                await self._wake(runtime, _wake_message(status))
        except Exception as err:  # noqa: BLE001 - the browser tools still work once it ended
            _LOG.warning("could not notify thread %s after a handoff: %s", thread_id, err)

    async def _wake(self, runtime: ManagedToolRuntime, message: BrowserHandoffWake) -> None:
        """Enqueue a run on the thread with the handoff's notification, and this run's context."""
        if runtime.server_info is None:
            return
        context = _forwarded_context(runtime.context)
        await get_client().runs.create(
            _thread_id(runtime),
            runtime.server_info.assistant_id,
            input={"messages": [cast("dict[str, Any]", message)]},
            multitask_strategy="enqueue",  # after a run that is going on
            **({"context": context} if context is not None else {}),
        )

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

        @tool
        async def browser_handoff(runtime: ManagedToolRuntime, reason: str) -> Command | str:
            """Hand the browser to the user; you'll be notified when they hand it back.

            Use it for what only they should do: log in, solve a CAPTCHA, approve a
            2FA prompt. Never ask for passwords. `reason` is shown to them as is: a
            few words on what to do, such as "Log in to LinkedIn".
            """
            return await self._hand_to_user(runtime, reason)

        @tool("browser_record", description=_RECORD_DESCRIPTION)
        async def record(
            runtime: ManagedToolRuntime,
            action: Literal["start", "stop"],
            path: str | None = None,
        ) -> list[dict[str, str]] | str:
            async def start(session: _Session) -> str:
                if session.take:
                    return "error: already recording. Stop this recording first."
                tab = await _newest_tab(session.stagehand.browser.context)
                recorder = recording.Recorder(session.cdp_url, _api()[1])
                await recorder.start(tab.page_id)
                session.take = _Take(recorder, runtime)
                width, height = recording.VIEWPORT
                return (
                    f"Recording. The viewport is {width}x{height} until you stop, "
                    "so take a browser_snapshot before acting on the page."
                )

            if action == "start":
                return await self._call(runtime, start)
            return await self._call(runtime, lambda s: _finish_recording(s, path))

        return [run, snapshot, screenshot, browser_handoff, record]


def _pending(state: BrowserState | Mapping[str, Any] | None) -> PendingBrowserHandoff | None:
    return state.get("mda_browser_handoff") if isinstance(state, Mapping) else None


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


def _wake_message(status: BrowserHandoffStatus) -> BrowserHandoffWake:
    """A notification in deepagents' task notification's shape: a user-role message
    framed as a system notification, its event under its own key."""
    notification: BrowserHandoffNotification = {"type": "browser_handoff", "status": status}
    return {
        "role": "user",
        "content": f"[SYSTEM NOTIFICATION - NOT USER INPUT]\n{_WAKE_TEXT[status]}",
        "mda_notification": notification,
    }


def _is_handoff_notification(message: BaseMessage) -> bool:
    notification = message.additional_kwargs.get(MDA_NOTIFICATION_KEY)
    return isinstance(notification, dict) and notification.get("type") == "browser_handoff"


async def _follow_tab(key: str) -> None:
    """Point ``key``'s recording at its window's newest tab, where the tools act."""
    if (session := _SESSIONS.get(key)) and session.take:
        with contextlib.suppress(Exception):  # it keeps the tab it had
            tab = await _newest_tab(session.stagehand.browser.context)
            await session.take.recorder.follow(tab.page_id)


async def _finish_recording(session: _Session, path: str | None) -> list[dict[str, str]] | str:
    """Stop the session's recording and save it to ``path``, or under recordings/.

    Returns what was saved and the first and last frames, for the model to check.
    """
    if (take := session.take) is None:
        return (
            "error: not recording. A recording ends when the run ends or you hand the "
            f"browser to the user, and is saved under {_RECORDINGS}/."
        )
    session.take = None
    captured = await take.recorder.stop()
    if not captured.frames:
        return "error: the recording has no frames: the tab never painted."
    video = await asyncio.to_thread(recording.encode, captured)
    path = path or f"{_RECORDINGS}/{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    saved = await _save(take.runtime, path, video)
    if saved.startswith("error:"):
        return saved
    happened = recording.events(captured)
    timeline = f"{posixpath.splitext(posixpath.normpath(path))[0]}.json"
    noted = await _save(take.runtime, timeline, json.dumps(happened, indent=1).encode())
    length = recording.Clock(captured).length
    text = f"{saved[:-1]}: {length:.0f}s, {len(video) / 1e6:.1f} MB. {noted}"
    if captured.capped:
        text += f" It stopped at {recording.MAX_S // 60} minutes of recording."
    text += "\nWhat happened, at video time:\n" + _summary(happened)
    text += "\nIts first and last frames follow; check them before sharing it."
    return [
        {"type": "text", "text": text},
        *(
            {"type": "image", "url": f"data:image/jpeg;base64,{base64.b64encode(f.jpeg).decode()}"}
            for f in (captured.frames[0], captured.frames[-1])
        ),
    ]


def _summary(happened: list[dict[str, object]], limit: int = 30) -> str:
    """A line per event, for the model to caption or describe the video with."""

    def line(event: dict[str, object]) -> str:
        at = f"{event['t']:>6.1f}s"
        if event["type"] == "navigate":
            return f"{at} opened {event['url']}"
        if event["type"] == "click":
            return f"{at} clicked {event['target'] or 'the page'} at ({event['x']}, {event['y']})"
        if event["type"] == "type":
            return f"{at} typed {event['text']!r} into {event['target'] or 'the page'}"
        return f"{at} pressed {event['key']}"

    lines = [line(event) for event in happened[:limit]]
    if len(happened) > limit:
        lines.append(f"... and {len(happened) - limit} more in the JSON.")
    return "\n".join(lines) or "(nothing)"


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
        return await _connect(key, window_name, name, cdp_url, local_url, headers)
    except Exception as err:
        if "already initialized" not in str(err):
            raise
    # A client that never closed (a crash, another replica) left its claim in the
    # extension; reinstalling it clears the claim without restarting Chrome.
    await _reinstall_extension(cdp_url, headers)
    return await _connect(key, window_name, name, cdp_url, local_url, headers)


async def _window(api: httpx.AsyncClient, browser: str, name: str) -> dict[str, Any]:
    """The browser's window ``name``, created for the agent if it has none."""
    windows = (await api.get(browser)).json().get("attachments") or []
    if window := next((w for w in windows if w["name"] == name), None):
        return window
    body = {"name": name, "controller": "agent"}
    return (await api.post(f"{browser}/attachments", json=body)).json()


async def _hand_over(key: str, window_name: str, idle_ttl_seconds: int) -> tuple[str, str, str]:
    """Give the user ``key``'s window, creating either as needed.

    Returns the browser's name, the window's id, and a short-lived link the user
    drives it with.
    """
    url, headers = _api()
    browsers = f"{url}/browsers"
    async with (
        httpx.AsyncClient(headers=headers, timeout=90, event_hooks={"response": [_raise]}) as api,
        AsyncSandboxClient(api_key=headers["X-Api-Key"], headers=headers) as sandboxes,
    ):
        name, _ = await _usable_browser(api, sandboxes, browsers, key, idle_ttl_seconds)
        window = await _window(api, f"{browsers}/{name}", window_name)
        window_api = f"{browsers}/{name}/attachments/{window['id']}"
        await api.post(f"{window_api}/controller", json={"controller": "user"})
        body = {"ttl_seconds": _HANDOFF_LINK_TTL_S, "embed_origins": await _embed_origins(api)}
        link = (await api.post(f"{window_api}/links", json=body)).json()["act_url"]
    return name, window["id"], link


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
    return _Session(window, stagehand, tools, artifacts, browser_name, cdp_url=cdp_url)


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
