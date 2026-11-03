"""Recordings of a browser window's tab: its screencast, encoded as an H.264 mp4.

A program for the browser's own sandbox. ``browser.py`` uploads it there and runs it
beside Chrome, on the window's local CDP port, so the screencast never crosses the
network and a take outlives the agent's client. It runs with PyAV, Pillow and
websockets, which ``browser.py`` installs there; the agent server never imports it.

    python3 _browser_recording.py start FOLDER CDP_URL TAB  # returns once recording
    python3 _browser_recording.py stop FOLDER  # returns once FOLDER holds the take

The recorder holds its own CDP connection to the window, beside Stagehand's, and
follows the window's newest tab, where the agent's tools act. It pins the viewport, so
takes match, and notes the pointer, clicks and typing in an isolated world, which the
page's own scripts cannot see. The cursor, clicks and keys are drawn onto the frames
when encoding, so nothing is drawn in the page. Typing into password, one-time-code
and card fields is masked as it is noted, and URLs lose their query and fragment.
"""

from __future__ import annotations

import asyncio
import base64
import bisect
import contextlib
import functools
import io
import itertools
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit, urlunsplit

import av
import websockets
from PIL import Image, ImageDraw, ImageFont, ImageOps

if TYPE_CHECKING:
    from collections.abc import Iterator

VIEWPORT = (1280, 800)
FPS = 15
MAX_HOLD_S = 1.5  # a pause, such as the model thinking between calls, plays this long at most
END_HOLD_S = 1.0  # the last frame, so the result is readable
MAX_S = 300  # of real time
_MAX_EVENTS = 20_000
# What the kept frames may hold in memory, in the browser's sandbox beside Chrome: a page
# that keeps changing reaches it before MAX_S.
_MAX_FRAME_BYTES = 1_000_000_000
_TIMEOUT_S = 30
_QUALITY = 90
# Frames keep up to this many device pixels per CSS pixel: a HiDPI browser paints two,
# and text scaled down to the viewport's CSS size blurs.
_DEVICE_SCALE = 2
_URL_CHARS = 200
_MASK = "*" * 8  # typing in a sensitive field, whatever its length; the font has "*"
_SKEW_S = 5.0  # page and frame times further off than this are not on the box's clock
_WORLD = "mda-recording"
_BINDING = "__mdaRecordEvent"
# Notes input in the page's top frame, in CSS pixels. Clicks and keys name their element
# by tag, id and label, never by value; sensitive fields' typing is masked here.
_SCRIPT = f"""
if (self === top && !globalThis.{_BINDING}Installed) {{
  globalThis.{_BINDING}Installed = true;
  // When it happened, on the box's clock, which Chrome's frame times share.
  const send = (e, event) => globalThis.{_BINDING}(JSON.stringify(
    {{...event, at: performance.timeOrigin + e.timeStamp}}));
  const secret = (el) => !!el && (el.type === "password" ||
    /one-time-code|cc-/.test(el.getAttribute?.("autocomplete") || ""));
  const describe = (el) => {{
    if (!(el instanceof Element)) return "";
    const t = el.closest("a,button,input,textarea,select,summary,label,[role]") || el;
    const name = (t.getAttribute("aria-label") || (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)
      ? "" : t.innerText) || t.getAttribute("placeholder") || t.getAttribute("title") ||
      t.getAttribute("name") || "").trim().replace(/\\s+/g, " ").slice(0, 60);
    return t.tagName.toLowerCase() + (t.id ? "#" + t.id : "") + (name ? ` "${{name}}"` : "");
  }};
  let moved = 0;
  addEventListener("pointermove", (e) => {{
    if (e.timeStamp - moved < 40) return;
    moved = e.timeStamp;
    send(e, {{type: "move", x: e.clientX, y: e.clientY}});
  }}, true);
  addEventListener("pointerdown", (e) => {{
    send(e, {{type: "click", x: e.clientX, y: e.clientY, target: describe(e.target)}});
  }}, true);
  addEventListener("keydown", (e) => {{
    const chord = e.ctrlKey || e.metaKey || e.altKey;
    if ((e.key.length === 1 && !chord) || /^(Shift|Control|Alt|Meta)$/.test(e.key)) return;
    const key = (e.metaKey ? "Cmd+" : "") + (e.ctrlKey ? "Ctrl+" : "") +
      (e.altKey ? "Alt+" : "") + (e.key === " " ? "Space" : e.key);
    send(e, {{type: "key", text: key, target: describe(document.activeElement)}});
  }}, true);
  addEventListener("input", (e) => {{
    if (!e.inputType?.startsWith("insert")) return;
    const text = e.data ?? e.target?.value ?? "";
    const typed = secret(e.target) ? "{_MASK}" : text;
    send(e, {{type: "input", text: typed, target: describe(e.target)}});
  }}, true);
}}
"""

EventKind = Literal["move", "click", "key", "input", "navigate"]


@dataclass
class Frame:
    at: float  # when Chrome painted it, in epoch seconds
    jpeg: bytes
    width: int  # the page's CSS size, which events are measured in
    height: int


@dataclass
class Event:
    at: float  # when it happened in the page, in epoch seconds
    kind: EventKind
    x: float = 0.0
    y: float = 0.0
    text: str = ""  # a key, typed text, or a url
    target: str = ""  # the element, e.g. `button "Save"`


@dataclass
class Take:
    """What a recording captured."""

    frames: list[Frame] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    capped: bool = False  # hit MAX_S or a limit, so it ends early
    closed: bool = False  # the browser went away, so it ends early


class Recorder:
    """Records the window at ``cdp_url`` from one tab on, following each newer tab."""

    def __init__(self, cdp_url: str) -> None:
        self.cdp_url = cdp_url  # the window's local port: no credentials
        self.take = Take()
        self.target: str | None = None
        self.done = asyncio.Event()  # told to stop, or the take hit a limit
        self.reader: asyncio.Task[None] | None = None
        self._session: str | None = None
        self._ws: websockets.ClientConnection | None = None
        self._ids = itertools.count(1)
        self._replies: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._tabs: list[str] = []  # the window's tabs, oldest first
        self._following = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()  # held until done
        self._acted = -math.inf  # when the last click, key, typing or navigation was noted
        self._frame_bytes = 0  # the kept frames' JPEGs

    async def start(self, target_id: str) -> None:
        self._ws = await websockets.connect(self.cdp_url, max_size=2**25)  # in 10 s or fails
        self.reader = asyncio.create_task(self._read(self._ws))
        targets = (await self._send("Target.getTargets"))["targetInfos"]
        self._tabs = [t["targetId"] for t in targets if t["type"] == "page"]
        await self._send("Target.setDiscoverTargets", discover=True)  # tabs opened later
        await self.follow(target_id)

    async def follow(self, target_id: str) -> None:
        """Record ``target_id`` from now on, if it is not the tab being recorded."""
        async with self._following:
            if target_id == self.target:
                return
            attached = await self._send("Target.attachToTarget", targetId=target_id, flatten=True)
            left, self._session, self.target = self._session, attached["sessionId"], target_id
            if left:
                with contextlib.suppress(Exception):  # the tab may have closed
                    await self._send("Target.detachFromTarget", sessionId=left)
            width, height = VIEWPORT
            on = self._on_tab
            await on(
                "Emulation.setDeviceMetricsOverride",
                width=width,
                height=height,
                deviceScaleFactor=0,
                mobile=False,
            )
            await on("Emulation.setFocusEmulationEnabled", enabled=True)  # paints unfocused tabs
            await on("Page.enable")  # so the script runs in each new document
            await on("Runtime.enable")
            await on("Runtime.addBinding", name=_BINDING, executionContextName=_WORLD)
            await on("Page.addScriptToEvaluateOnNewDocument", source=_SCRIPT, worldName=_WORLD)
            tree = (await on("Page.getFrameTree"))["frameTree"]["frame"]
            self._navigated(time.time(), tree.get("url", ""))
            world = await on("Page.createIsolatedWorld", frameId=tree["id"], worldName=_WORLD)
            await on("Runtime.evaluate", expression=_SCRIPT, contextId=world["executionContextId"])
            await on(
                "Page.startScreencast",
                format="jpeg",
                quality=_QUALITY,
                maxWidth=width * _DEVICE_SCALE,
                maxHeight=height * _DEVICE_SCALE,
            )

    async def stop(self) -> Take:
        """Stop recording, ending on how the tab looks now: the last action's result may
        not have reached a screencast frame. Closing drops the viewport and focus overrides.
        """
        frames = self.take.frames
        if self._session and not self.take.capped:
            with contextlib.suppress(Exception):  # the tab may have closed
                shot = await self._on_tab("Page.captureScreenshot", format="jpeg", quality=_QUALITY)
                width, height = (frames[-1].width, frames[-1].height) if frames else VIEWPORT
                frames.append(Frame(time.time(), base64.b64decode(shot["data"]), width, height))
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.reader  # so a window that went away is not logged as a crash
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
        return self.take

    async def _on_tab(self, method: str, **params: object) -> dict[str, Any]:
        return await self._send(method, session=self._session, **params)

    async def _send(
        self, method: str, session: str | None = None, **params: object
    ) -> dict[str, Any]:
        if self._ws is None:
            msg = "the recording stopped"
            raise RuntimeError(msg)
        id_ = next(self._ids)
        message: dict[str, object] = {"id": id_, "method": method, "params": params}
        if session:
            message["sessionId"] = session
        self._replies[id_] = reply = asyncio.get_running_loop().create_future()
        try:
            await self._ws.send(json.dumps(message))
            result = await asyncio.wait_for(reply, _TIMEOUT_S)
        finally:
            self._replies.pop(id_, None)
        if "error" in result:
            msg = f"CDP {method} failed: {result['error']}"
            raise RuntimeError(msg)
        return result.get("result", {})

    async def _read(self, ws: websockets.ClientConnection) -> None:
        async for raw in ws:
            message = json.loads(raw)
            if (id_ := message.get("id")) is not None:
                if (reply := self._replies.get(id_)) and not reply.done():
                    reply.set_result(message)
                continue
            method, params, now = message.get("method"), message.get("params", {}), time.time()
            if method == "Target.targetCreated":
                self._opened(params["targetInfo"])
            elif method == "Target.targetDestroyed":
                self._closed(params["targetId"])
            elif message.get("sessionId") != self._session:
                continue  # a tab we stopped following
            elif method == "Page.screencastFrame":
                await self._frame(ws, params, now)
            elif method == "Runtime.bindingCalled" and params["name"] == _BINDING:
                with contextlib.suppress(ValueError, KeyError, TypeError):
                    self._note(_event(now, json.loads(params["payload"])))
            elif method == "Page.frameNavigated" and not params["frame"].get("parentId"):
                self._navigated(now, params["frame"]["url"])

    def _opened(self, target: dict[str, Any]) -> None:
        """A tab opened: follow it, as the agent's tools act in the newest tab."""
        if target.get("type") == "page" and target["targetId"] not in self._tabs:
            self._tabs.append(target["targetId"])
            self._spawn(self._follow_newest())

    def _closed(self, target_id: str) -> None:
        with contextlib.suppress(ValueError):
            self._tabs.remove(target_id)
        if target_id == self.target:
            self._spawn(self._follow_newest())

    async def _follow_newest(self) -> None:
        if self._tabs:
            with contextlib.suppress(Exception):  # it keeps the tab it had
                await self.follow(self._tabs[-1])

    def _spawn(self, work: Any) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _frame(
        self, ws: websockets.ClientConnection, params: dict[str, Any], now: float
    ) -> None:
        if self.take.capped:
            return  # not acked, so Chrome sends no more
        meta = params["metadata"]
        at = _on_clock(meta.get("timestamp"), now)  # painted, which can be a while before
        jpeg = base64.b64decode(params["data"])
        frame = Frame(at, jpeg, meta["deviceWidth"], meta["deviceHeight"])
        frames = self.take.frames
        last = frames[-1] if frames else None
        # The video shows a frame per tick: the newest, if nothing happened since the last.
        if last and int(last.at * FPS) == int(at * FPS) and self._acted <= last.at <= at:
            self._frame_bytes -= len(frames.pop().jpeg)
        frames.append(frame)
        self._frame_bytes += len(jpeg)
        if self._frame_bytes >= _MAX_FRAME_BYTES:
            self.take.capped = True
            self.done.set()
            return
        ack = {"sessionId": params["sessionId"]}
        message = {"id": next(self._ids), "method": "Page.screencastFrameAck", "params": ack}
        await ws.send(json.dumps({**message, "sessionId": self._session}))

    def _navigated(self, at: float, url: str) -> None:
        if url and url != "about:blank":  # a tab before its first page
            self._note(Event(at, "navigate", text=_bare(url)))

    def _note(self, event: Event) -> None:
        if not self.take.capped and len(self.take.events) < _MAX_EVENTS:
            self.take.events.append(event)
            if event.kind != "move":
                self._acted = event.at


def _event(now: float, payload: dict[str, Any]) -> Event:
    at = payload.get("at")
    return Event(
        _on_clock(at / 1000 if isinstance(at, int | float) else None, now),
        payload["type"],
        float(payload.get("x", 0)),
        float(payload.get("y", 0)),
        str(payload.get("text", ""))[:200],
        str(payload.get("target", ""))[:120],
    )


def _on_clock(at: object, now: float) -> float:
    """When something happened by Chrome's own word, if that is on the box's clock.

    Its frames arrive a while after they are painted, and the page's events a little
    after they happen, so arrival would put the frame that shows typing after the click
    that follows it.
    """
    if isinstance(at, int | float) and abs(at - now) < _SKEW_S:
        return float(at)
    return now


def _bare(url: str) -> str:
    """``url`` without what can carry a secret: its query, fragment and credentials."""
    parts = urlsplit(url)
    if parts.scheme == "data":  # the page itself is in the url
        return f"data:{parts.path.partition(',')[0]},..."
    netloc = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))[:_URL_CHARS]


# --- the video's timeline ------------------------------------------------------------


# How long the video gives each input to read, however fast the agent was: an
# agent often clicks, types and submits in one call, within milliseconds.
_READ_S = {"click": 0.7, "key": 0.35}
_CHAR_READ_S = 0.07
_TYPING_READ_S = (0.1, 1.2)  # one burst of typed text: shortest, longest


class Clock:
    """The video's clock: real time with pauses cut and bursts of input spread out.

    A pause, such as the model thinking between calls, plays for at most
    ``MAX_HOLD_S``. Each click, key or bit of typing gets time to read before the
    next thing happens, so a login done in one call plays as steps. Typing gets it
    once the frame that shows it is up, so its caption keeps pace with the page.
    """

    def __init__(self, take: Take) -> None:
        moments: list[tuple[float, int, Frame | Event]] = sorted(
            [(f.at, 0, f) for f in take.frames]
            + [(e.at, 1, e) for e in take.events if e.kind != "move"],
            key=lambda moment: moment[:2],
        )
        self.reals: list[float] = []
        self.videos: list[float] = []
        self._placed: dict[int, float] = {}  # each frame's and event's own time
        video, need, typing = 0.0, 0.0, 0.0
        for at, _, moment in moments:
            shows_typing = isinstance(moment, Frame) and typing
            if self.reals:
                video += max(min(at - self.reals[-1], MAX_HOLD_S), 0.0 if shows_typing else need)
            self.reals.append(at)
            self.videos.append(video)
            self._placed[id(moment)] = video
            if isinstance(moment, Frame):
                need, typing = typing, 0.0
            else:
                need = _read_time(moment)
                typing = need if moment.kind == "input" else 0.0
        self.length = video + max(END_HOLD_S, need)
        self.frame_starts = [self._placed[id(f)] for f in take.frames]

    def of(self, event: Event) -> float:
        """Where ``event`` lands in the video."""
        if (placed := self._placed.get(id(event))) is not None:
            return placed
        return self.at(event.at)  # a pointer move, which takes no time of its own

    def at(self, real: float) -> float:
        """Where a moment of real time lands in the video."""
        index = bisect.bisect_right(self.reals, real) - 1
        if index < 0:
            return 0.0
        span = self.videos[index + 1] if index + 1 < len(self.videos) else self.length
        return self.videos[index] + min(real - self.reals[index], span - self.videos[index])


def _read_time(event: Event) -> float:
    if event.kind == "input":
        low, high = _TYPING_READ_S
        return min(max(len(event.text) * _CHAR_READ_S, low), high)
    return _READ_S.get(event.kind, 0.0)


_TYPING_GAP_S = 1.5  # typing further apart than this is another burst


def events(take: Take) -> list[dict[str, object]]:
    """What happened, at video time ``t``: for captions, chapters or a PR's notes.

    Clicks keep their CSS point in the pinned viewport; a burst of typing into one
    field is one ``type`` event; pointer moves are left out.
    """
    clock = Clock(take)
    out: list[dict[str, object]] = []
    typed: dict[str, object] | None = None
    last_typed: Event | None = None
    last_url = None
    for event in take.events:
        t = round(clock.of(event), 2)
        if event.kind == "input" and typed and last_typed and _one_burst(last_typed, event):
            typed["text"] = _typed(str(typed["text"]), event.text)
        elif event.kind == "input":
            typed = {"t": t, "type": "type", "text": event.text, "target": event.target}
            out.append(typed)
        elif event.kind == "click":
            out.append(
                {
                    "t": t,
                    "type": "click",
                    "x": round(event.x),
                    "y": round(event.y),
                    "target": event.target,
                }
            )
        elif event.kind == "key":
            out.append({"t": t, "type": "key", "key": event.text, "target": event.target})
        elif event.kind == "navigate" and event.text != last_url:
            out.append({"t": t, "type": "navigate", "url": event.text})
            last_url = event.text
        if event.kind == "input":
            last_typed = event
        elif event.kind != "move":
            typed = None
    return out


def summary(happened: list[dict[str, object]], limit: int = 30) -> str:
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


# --- encoding ------------------------------------------------------------------------


def encode(take: Take) -> bytes:
    """An H.264 mp4 of ``take``, with its cursor, clicks and typing drawn on."""
    if not take.frames:
        msg = "the recording has no frames"
        raise ValueError(msg)
    size = _size(take)
    overlay = _Overlay(take, size)
    # On disk: `faststart`, which lets a page play it before it has all loaded, reopens
    # the file to move its index to the front.
    with tempfile.TemporaryDirectory(prefix="mda-recording-") as tmp:
        out = Path(tmp, "out.mp4")
        with av.open(str(out), mode="w", options={"movflags": "+faststart"}) as container:
            stream = container.add_stream(
                "libx264", rate=FPS, options={"crf": "20", "preset": "veryfast"}
            )
            stream.width, stream.height = size
            stream.pix_fmt = "yuv420p"
            for image in overlay.frames():
                for packet in stream.encode(av.VideoFrame.from_image(image)):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        return out.read_bytes()


def _size(take: Take) -> tuple[int, int]:
    """The video's size: its first frame's, with even sides, which yuv420p needs."""
    first = Image.open(io.BytesIO(take.frames[0].jpeg))
    return first.width // 2 * 2, first.height // 2 * 2


_GLIDE_S = (0.25, 0.6)  # how long the cursor takes to reach a point: near, far
_SAME_POINT_S = 0.1  # a move and the click it leads to are one point
_CLICK_S = 0.6  # how long a click's ripple shows
_PRESS_S = 0.15  # how long the cursor shrinks as it clicks
_CAPTION_HOLD_S = 1.2  # how long typing stays up after the last key
_CAPTION_CHARS = 48
_ACCENT = (37, 99, 235)
# A classic arrow, tip at (0, 0), in CSS pixels.
_ARROW = [(0, 0), (0, 21), (5, 16), (9, 25), (13, 23), (9, 15), (16, 15)]


@dataclass
class _Point:
    t: float  # video time it arrives
    x: float
    y: float


class _Overlay:
    """Draws ``take`` frame by frame: the cursor gliding between points, clicks, typing."""

    def __init__(self, take: Take, size: tuple[int, int]) -> None:
        self.take = take
        self.size = size
        self.clock = Clock(take)
        frame = take.frames[0]
        self.scale = size[0] / frame.width if frame.width else 1.0  # video pixels per CSS pixel
        self.font = ImageFont.load_default(size=22 * self.scale)
        timed = [(self.clock.of(e), e) for e in take.events]
        self.clicks = [(t, e) for t, e in timed if e.kind == "click"]
        self.points = _points(timed, frame.width, frame.height)
        self.typing = [(t, e) for t, e in timed if e.kind in ("key", "input")]

    def frames(self) -> Iterator[Image.Image]:
        fitted = functools.lru_cache(maxsize=1)(lambda i: _fit(self.take.frames[i].jpeg, self.size))
        for n in range(math.ceil(self.clock.length * FPS)):
            now = n / FPS
            image = fitted(max(bisect.bisect_right(self.clock.frame_starts, now) - 1, 0)).copy()
            draw = ImageDraw.Draw(image, "RGBA")
            self._draw_clicks(draw, now)
            self._draw_cursor(draw, now)
            self._draw_typing(draw, now)
            yield image

    def _draw_clicks(self, draw: ImageDraw.ImageDraw, now: float) -> None:
        for shown, click in self.clicks:
            if not 0 <= (age := now - shown) < _CLICK_S:
                continue
            s = self.scale
            x, y, fade = click.x * s, click.y * s, 1 - age / _CLICK_S
            radius = (8 + 26 * (1 - fade**3)) * s  # eases out
            box = (x - radius, y - radius, x + radius, y + radius)
            draw.ellipse(box, outline=(255, 255, 255, int(230 * fade)), width=round(7 * s))
            draw.ellipse(box, outline=(*_ACCENT, int(255 * fade)), width=round(3 * s))
            dot = 9 * s
            draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(*_ACCENT, int(90 * fade)))

    def _draw_cursor(self, draw: ImageDraw.ImageDraw, now: float) -> None:
        x, y = (v * self.scale for v in _cursor_at(self.points, now))
        pressed = any(0 <= now - shown < _PRESS_S for shown, _ in self.clicks)
        s = self.scale
        size = s * (0.85 if pressed else 1.0)
        arrow = [(x + px * size, y + py * size) for px, py in _ARROW]
        draw.polygon([(px + 2 * s, py + 3 * s) for px, py in arrow], fill=(0, 0, 0, 70))  # shadow
        outline = (20, 20, 20, 255)
        draw.polygon(arrow, fill=(255, 255, 255, 255), outline=outline, width=round(2 * s))

    def _draw_typing(self, draw: ImageDraw.ImageDraw, now: float) -> None:
        text = self._caption(now)
        if not text:
            return
        s = self.scale
        left, top, right, bottom = draw.textbbox((0, 0), text, font=self.font)
        pad_x, pad_y = 18 * s, 12 * s
        width, height = right - left + 2 * pad_x, bottom - top + 2 * pad_y
        x0, y0 = (self.size[0] - width) / 2, self.size[1] - height - 36 * s
        draw.rounded_rectangle(
            (x0, y0, x0 + width, y0 + height), radius=height / 2, fill=(17, 17, 17, 210)
        )
        draw.text((x0 + pad_x - left, y0 + pad_y - top), text, font=self.font, fill="white")

    def _caption(self, now: float) -> str:
        """The burst of typing that ``now`` falls in, as typed so far."""
        shown = [(t, e) for t, e in self.typing if t <= now]
        if not shown or now - shown[-1][0] > _CAPTION_HOLD_S:
            return ""
        first = len(shown) - 1
        while first and _one_burst(shown[first - 1][1], shown[first][1]):
            first -= 1
        text = ""
        for _, event in shown[first:]:
            text = _typed(text, event.text) if event.kind == "input" else f"{text} [{event.text}] "
        text = text.strip()
        return text if len(text) <= _CAPTION_CHARS else "..." + text[-_CAPTION_CHARS:]


def _one_burst(before: Event, after: Event) -> bool:
    """Typing close together, into one field."""
    return after.at - before.at <= _TYPING_GAP_S and after.target == before.target


def _typed(text: str, more: str) -> str:
    """``more`` typed after ``text``; a masked field shows one mask, however long."""
    return text if more == _MASK and text.endswith(_MASK) else text + more


def _points(timed: list[tuple[float, Event]], width: int, height: int) -> list[_Point]:
    """Where the cursor is headed, and by when: the pointer's moves and clicks."""
    points = [_Point(0.0, width / 2, height / 2)]
    for t, event in timed:
        if event.kind not in ("move", "click"):
            continue
        if t - points[-1].t < _SAME_POINT_S and len(points) > 1:
            points[-1] = _Point(points[-1].t, event.x, event.y)  # the same gesture
        else:
            points.append(_Point(t, event.x, event.y))
    return points


def _cursor_at(points: list[_Point], now: float) -> tuple[float, float]:
    """The cursor at ``now``, gliding into each point along a slight curve.

    The glide ends as the point is reached, so the page's pauses make room for it
    and the video does not run long.
    """
    index = bisect.bisect_right([p.t for p in points], now)
    if index >= len(points):
        return points[-1].x, points[-1].y
    before, to = points[index - 1], points[index]
    distance = math.hypot(to.x - before.x, to.y - before.y)
    glide = min(_GLIDE_S[0] + distance / 2500, _GLIDE_S[1], to.t - before.t)
    if glide <= 0 or now < to.t - glide:
        return before.x, before.y
    k = _ease_in_out((now - (to.t - glide)) / glide)
    # Bow the path to one side, alternating, and settle with a slight wobble.
    bend = (0.12 if index % 2 else -0.12) * distance
    nx, ny = (-(to.y - before.y), to.x - before.x) if distance else (0.0, 0.0)
    norm = distance or 1.0
    arc = 4 * k * (1 - k) * bend + 2.5 * math.sin(k * math.pi * 3) * (1 - k)
    x = before.x + (to.x - before.x) * k + nx / norm * arc
    y = before.y + (to.y - before.y) * k + ny / norm * arc
    return x, y


def _ease_in_out(k: float) -> float:
    return 4 * k**3 if k < 1 / 2 else 1 - (-2 * k + 2) ** 3 / 2


def _fit(jpeg: bytes, size: tuple[int, int]) -> Image.Image:
    return ImageOps.pad(Image.open(io.BytesIO(jpeg)).convert("RGB"), size, color="black")


def _still(jpeg: bytes, size: tuple[int, int]) -> bytes:
    """``jpeg`` at ``size``, for the model to look at."""
    if Image.open(io.BytesIO(jpeg)).size == size:
        return jpeg
    out = io.BytesIO()
    _fit(jpeg, size).save(out, "JPEG", quality=85)
    return out.getvalue()


# --- the program, in the browser's sandbox ---------------------------------------------

_RESULT = "result.json"  # written last: how long the video is, or why there is none
_SAVE_TIMEOUT_S = 600  # to stop and encode a take


def main(argv: list[str]) -> int:
    """``start``, ``stop`` or, in the background, ``record`` a take in a folder.

    ``start`` and ``stop`` print one JSON object, with ``error`` if they failed.
    """
    match argv:
        case ["start", folder, cdp_url, tab]:
            return _start(Path(folder), cdp_url, tab)
        case ["stop", folder]:
            return _stop(Path(folder))
        case ["record", folder, cdp_url, tab]:
            asyncio.run(_record(Path(folder), cdp_url, tab))
            return 0
    sys.stderr.write(__doc__ or "")
    return 2


def _start(folder: Path, cdp_url: str, tab: str) -> int:
    """Record in the background, returning once the screencast has begun."""
    folder.mkdir(parents=True, exist_ok=True)  # browser.py runs this program from it
    with (folder / "log").open("wb") as log:
        child = subprocess.Popen(  # noqa: S603 - this program, with the caller's arguments
            [sys.executable, __file__, "record", str(folder), cdp_url, tab],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # outlives this command
        )
    (folder / "pid").write_text(str(child.pid))
    deadline = time.monotonic() + _TIMEOUT_S
    while not (folder / "ready").exists():
        if child.poll() is not None or time.monotonic() > deadline:
            child.kill()
            return _reply({"error": _failure(folder, "the recorder did not start")})
        time.sleep(0.05)
    return _reply({"recording": True})


def _stop(folder: Path) -> int:
    """Stop the take, unless it already ended, and wait until it is saved."""
    result = folder / _RESULT
    pid = int((folder / "pid").read_text())
    if not result.exists():
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + _SAVE_TIMEOUT_S
    while not result.exists():
        if not _alive(pid) or time.monotonic() > deadline:
            return _reply({"error": _failure(folder, "the recorder stopped without saving")})
        time.sleep(0.1)
    sys.stdout.write(result.read_text() + "\n")
    return 0


async def _record(folder: Path, cdp_url: str, tab: str) -> None:
    """Record until told to stop or a limit, then save the take into ``folder``."""
    recorder = Recorder(cdp_url)
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, recorder.done.set)
    try:
        await recorder.start(tab)
    except Exception as err:  # noqa: BLE001 - reported to the agent
        _result(folder, {"error": f"could not record the tab: {type(err).__name__}: {err}"})
        return
    (folder / "ready").touch()
    done = asyncio.create_task(recorder.done.wait())
    ends = [done, recorder.reader] if recorder.reader else [done]  # or the window went away
    ended, _ = await asyncio.wait(ends, timeout=MAX_S, return_when=asyncio.FIRST_COMPLETED)
    if not ended:
        recorder.take.capped = True  # MAX_S
    elif not recorder.done.is_set():
        recorder.take.closed = True
    done.cancel()
    take = await recorder.stop()
    try:
        _result(folder, _save(folder, take))
    except Exception as err:  # noqa: BLE001 - reported to the agent
        _result(folder, {"error": f"could not save the recording: {type(err).__name__}: {err}"})


def _save(folder: Path, take: Take) -> dict[str, object]:
    """Encode ``take`` beside its timeline and its first and last frames."""
    if not take.frames:
        return {"error": "the recording has no frames: the tab never painted."}
    take.frames.sort(key=lambda frame: frame.at)  # they can arrive out of the order painted
    (folder / "take.mp4").write_bytes(encode(take))
    happened = events(take)
    (folder / "take.json").write_text(json.dumps(happened, indent=1))
    # At the page's CSS size: the model reads them, and they stay in the thread.
    first = take.frames[0]
    page = (first.width, first.height) if first.width and first.height else _size(take)
    (folder / "first.jpg").write_bytes(_still(first.jpeg, page))
    (folder / "last.jpg").write_bytes(_still(take.frames[-1].jpeg, page))
    return {
        "seconds": round(Clock(take).length, 2),
        "early": _early(take),
        "summary": summary(happened),
    }


def _early(take: Take) -> str:
    """Why ``take`` ended before it was told to stop, for the model; empty if it did not."""
    if take.closed:
        return "It stopped early, when the browser closed."
    if take.capped:
        return (
            f"It stopped early, at the recorder's limit: {MAX_S // 60} minutes, or less"
            " on a page that kept changing."
        )
    return ""


def _result(folder: Path, result: dict[str, object]) -> None:
    partial = folder / f"{_RESULT}.partial"
    partial.write_text(json.dumps(result))
    partial.replace(folder / _RESULT)  # whole, or not there


def _reply(reply: dict[str, object]) -> int:
    sys.stdout.write(json.dumps(reply) + "\n")
    return 1 if "error" in reply else 0


def _failure(folder: Path, otherwise: str) -> str:
    """Why the recorder failed: its own word, or the last line it logged."""
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        return str(json.loads((folder / _RESULT).read_text())["error"])
    with contextlib.suppress(OSError, IndexError):
        last = (folder / "log").read_text(errors="replace").strip().splitlines()[-1]
        return f"{otherwise}: {last[-300:]}"
    return otherwise


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with contextlib.suppress(OSError, IndexError):  # Linux: a zombie no one reaped
        return Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2][:1] != "Z"
    return True


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
