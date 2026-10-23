"""Recordings of a browser window's tab: its screencast, encoded as an H.264 mp4.

A recorder holds its own CDP connection to the window, beside Stagehand's. It pins
the viewport, so takes match, and notes the pointer, clicks and typing in an
isolated world, which the page's own scripts cannot see. The cursor, clicks and
keys are drawn onto the frames when encoding, so nothing is drawn in the page.
Typing into password, one-time-code and card fields is masked as it is noted.
"""

from __future__ import annotations

import asyncio
import base64
import bisect
import contextlib
import io
import itertools
import json
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import av
import websockets
from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from collections.abc import Iterator

VIEWPORT = (1280, 800)
FPS = 15
MAX_HOLD_S = 1.5  # a pause, such as the model thinking between calls, plays this long at most
END_HOLD_S = 1.0  # the last frame, so the result is readable
MAX_S = 300  # of real time; frames after it are dropped
_MAX_FRAMES = 3000
_MAX_EVENTS = 20_000
_TIMEOUT_S = 30
_WORLD = "mda-recording"
_BINDING = "__mdaRecordEvent"
# Notes input in the page's top frame, in CSS pixels. Clicks and keys name their element
# by tag, id and label, never by value; sensitive fields' typing is masked here.
_SCRIPT = f"""
if (self === top && !globalThis.{_BINDING}Installed) {{
  globalThis.{_BINDING}Installed = true;
  const send = (event) => globalThis.{_BINDING}(JSON.stringify(event));
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
    send({{type: "move", x: e.clientX, y: e.clientY}});
  }}, true);
  addEventListener("pointerdown", (e) => {{
    send({{type: "click", x: e.clientX, y: e.clientY, target: describe(e.target)}});
  }}, true);
  addEventListener("keydown", (e) => {{
    const chord = e.ctrlKey || e.metaKey || e.altKey;
    if ((e.key.length === 1 && !chord) || /^(Shift|Control|Alt|Meta)$/.test(e.key)) return;
    const key = (e.metaKey ? "Cmd+" : "") + (e.ctrlKey ? "Ctrl+" : "") +
      (e.altKey ? "Alt+" : "") + (e.key === " " ? "Space" : e.key);
    send({{type: "key", text: key, target: describe(document.activeElement)}});
  }}, true);
  addEventListener("input", (e) => {{
    if (!e.inputType?.startsWith("insert")) return;
    const text = e.data ?? e.target?.value ?? "";
    send({{type: "input", text: secret(e.target) ? "*".repeat(text.length) : text,
      target: describe(e.target)}});
  }}, true);
}}
"""

EventKind = Literal["move", "click", "key", "input", "navigate"]


@dataclass
class Frame:
    at: float  # when it arrived, on the event loop's clock
    jpeg: bytes
    width: int  # the page's CSS size, which events are measured in
    height: int


@dataclass
class Event:
    at: float
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


class Recorder:
    """Records the tab ``target_id`` of the window at ``cdp_url``, following later tabs."""

    def __init__(self, cdp_url: str, headers: dict[str, str]) -> None:
        self.cdp_url = cdp_url
        self.headers = headers
        self.take = Take()
        self.target: str | None = None
        self._session: str | None = None
        self._ws: websockets.ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._ids = itertools.count(1)
        self._replies: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._started = 0.0

    async def start(self, target_id: str) -> None:
        self._ws = await asyncio.wait_for(
            websockets.connect(self.cdp_url, additional_headers=self.headers, max_size=2**25),
            _TIMEOUT_S,
        )
        self._reader = asyncio.create_task(self._read(self._ws))
        self._started = asyncio.get_running_loop().time()
        try:
            await self.follow(target_id)
        except BaseException:
            await self.stop()
            raise

    async def follow(self, target_id: str) -> None:
        """Record ``target_id`` from now on, if it is not the tab being recorded."""
        if target_id == self.target:
            return
        if self._session:
            with contextlib.suppress(Exception):  # the tab may have closed
                await self._send("Target.detachFromTarget", sessionId=self._session)
        attached = await self._send("Target.attachToTarget", targetId=target_id, flatten=True)
        self._session, self.target = attached["sessionId"], target_id
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
        self._note(Event(asyncio.get_running_loop().time(), "navigate", text=tree.get("url", "")))
        world = await on("Page.createIsolatedWorld", frameId=tree["id"], worldName=_WORLD)
        await on("Runtime.evaluate", expression=_SCRIPT, contextId=world["executionContextId"])
        await on(
            "Page.startScreencast", format="jpeg", quality=70, maxWidth=width, maxHeight=height
        )

    async def stop(self) -> Take:
        """Stop recording; detaching drops the viewport and focus overrides."""
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        for reply in self._replies.values():
            reply.cancel()
        self._ws = self._reader = None
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
        loop = asyncio.get_running_loop()
        async for raw in ws:
            message = json.loads(raw)
            if (id_ := message.get("id")) is not None:
                if (reply := self._replies.get(id_)) and not reply.done():
                    reply.set_result(message)
                continue
            if message.get("sessionId") != self._session:
                continue  # a tab we stopped following
            method, params, now = message.get("method"), message.get("params", {}), loop.time()
            if method == "Page.screencastFrame":
                await self._frame(ws, params, now)
            elif method == "Runtime.bindingCalled" and params["name"] == _BINDING:
                with contextlib.suppress(ValueError, KeyError, TypeError):
                    self._note(_event(now, json.loads(params["payload"])))
            elif method == "Page.frameNavigated" and not params["frame"].get("parentId"):
                self._note(Event(now, "navigate", text=params["frame"]["url"]))

    async def _frame(
        self, ws: websockets.ClientConnection, params: dict[str, Any], now: float
    ) -> None:
        if self._full(now):
            return  # not acked, so Chrome sends no more
        meta = params["metadata"]
        jpeg = base64.b64decode(params["data"])
        self.take.frames.append(Frame(now, jpeg, meta["deviceWidth"], meta["deviceHeight"]))
        ack = {"sessionId": params["sessionId"]}
        message = {"id": next(self._ids), "method": "Page.screencastFrameAck", "params": ack}
        await ws.send(json.dumps({**message, "sessionId": self._session}))

    def _note(self, event: Event) -> None:
        if not self._full(event.at) and len(self.take.events) < _MAX_EVENTS:
            self.take.events.append(event)

    def _full(self, now: float) -> bool:
        if len(self.take.frames) >= _MAX_FRAMES or now - self._started > MAX_S:
            self.take.capped = True
        return self.take.capped


def _event(at: float, payload: dict[str, Any]) -> Event:
    kind = payload["type"]
    if kind not in ("move", "click", "key", "input"):
        msg = f"unknown event {kind!r}"
        raise ValueError(msg)
    return Event(
        at,
        kind,
        float(payload.get("x", 0)),
        float(payload.get("y", 0)),
        str(payload.get("text", ""))[:200],
        str(payload.get("target", ""))[:120],
    )


# --- the video's timeline ------------------------------------------------------------


# How long the video gives each input to read, however fast the agent was: an
# agent often clicks, types and submits in one call, within milliseconds.
_CLICK_READ_S = 0.7
_KEY_READ_S = 0.35
_CHAR_READ_S = 0.07
_TYPING_READ_S = (0.1, 1.2)  # one burst of typed text: shortest, longest


class Clock:
    """The video's clock: real time with pauses cut and bursts of input spread out.

    A pause, such as the model thinking between calls, plays for at most
    ``MAX_HOLD_S``. Each click, key or bit of typing gets time to read before the
    next thing happens, so a login done in one call plays as steps.
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
        video, need = 0.0, 0.0
        for at, _, moment in moments:
            if self.reals:
                video += max(min(at - self.reals[-1], MAX_HOLD_S), need)
            self.reals.append(at)
            self.videos.append(video)
            self._placed[id(moment)] = video
            need = _read_time(moment) if isinstance(moment, Event) else 0.0
        self.length = video + max(END_HOLD_S, need)
        self.frame_starts = [self._placed[id(f)] for f in take.frames]  # in arrival order

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
    if event.kind == "click":
        return _CLICK_READ_S
    if event.kind == "key":
        return _KEY_READ_S
    if event.kind == "input":
        low, high = _TYPING_READ_S
        return min(max(len(event.text) * _CHAR_READ_S, low), high)
    return 0.0


_TYPING_GAP_S = 1.5  # typing further apart than this is another burst


def events(take: Take) -> list[dict[str, object]]:
    """What happened, at video time ``t``: for captions, chapters or a PR's notes.

    Clicks keep their CSS point in the pinned viewport; a burst of typing is one
    ``type`` event; pointer moves are left out.
    """
    if not take.frames:
        return []
    clock = Clock(take)
    out: list[dict[str, object]] = []
    typed: dict[str, object] | None = None
    last_typed, last_url = -math.inf, None
    for event in take.events:
        t = round(clock.of(event), 2)
        if event.kind == "input" and typed and event.at - last_typed < _TYPING_GAP_S:
            typed["text"] = f"{typed['text']}{event.text}"
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
            last_typed = event.at
        elif event.kind != "move":
            typed = None
    return out


# --- encoding ------------------------------------------------------------------------


def encode(take: Take) -> bytes:
    """An H.264 mp4 of ``take``, with its cursor, clicks and typing drawn on."""
    if not take.frames:
        msg = "the recording has no frames"
        raise ValueError(msg)
    first = Image.open(io.BytesIO(take.frames[0].jpeg))
    size = (first.width // 2 * 2, first.height // 2 * 2)  # yuv420p needs even sides
    overlay = _Overlay(take, size)
    # On disk: `faststart`, which lets a page play it before it has all loaded, reopens
    # the file to move its index to the front.
    with tempfile.TemporaryDirectory(prefix="mda-recording-") as tmp:
        out = Path(tmp, "out.mp4")
        with av.open(str(out), mode="w", options={"movflags": "+faststart"}) as container:
            stream = container.add_stream(
                "libx264", rate=FPS, options={"crf": "28", "preset": "veryfast"}
            )
            stream.width, stream.height = size
            stream.pix_fmt = "yuv420p"
            for image in overlay.frames():
                for packet in stream.encode(av.VideoFrame.from_image(image)):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        return out.read_bytes()


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
        self.scale = size[0] / frame.width if frame.width else 1.0
        self.font = ImageFont.load_default(size=22)
        timed = [(self.clock.of(e), e) for e in take.events]
        self.clicks = [(t, e) for t, e in timed if e.kind == "click"]
        self.points = _points(timed, frame.width, frame.height)
        self.typing = [(t, e) for t, e in timed if e.kind in ("key", "input")]

    def frames(self) -> Iterator[Image.Image]:
        decoded: dict[int, Image.Image] = {}
        for n in range(math.ceil(self.clock.length * FPS)):
            now = n / FPS
            index = max(bisect.bisect_right(self.clock.frame_starts, now) - 1, 0)
            if index not in decoded:
                decoded.clear()
                decoded[index] = _fit(self.take.frames[index].jpeg, self.size)
            image = decoded[index].copy()
            draw = ImageDraw.Draw(image, "RGBA")
            self._draw_clicks(draw, now)
            self._draw_cursor(draw, now)
            self._draw_typing(draw, now)
            yield image

    def _draw_clicks(self, draw: ImageDraw.ImageDraw, now: float) -> None:
        for shown, click in self.clicks:
            if not 0 <= (age := now - shown) < _CLICK_S:
                continue
            x, y, fade = click.x * self.scale, click.y * self.scale, 1 - age / _CLICK_S
            radius = 8 + 26 * _ease_out(age / _CLICK_S)
            box = (x - radius, y - radius, x + radius, y + radius)
            draw.ellipse(box, outline=(255, 255, 255, int(230 * fade)), width=7)
            draw.ellipse(box, outline=(*_ACCENT, int(255 * fade)), width=3)
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=(*_ACCENT, int(90 * fade)))

    def _draw_cursor(self, draw: ImageDraw.ImageDraw, now: float) -> None:
        x, y = (v * self.scale for v in _cursor_at(self.points, now))
        pressed = any(0 <= now - shown < _PRESS_S for shown, _ in self.clicks)
        size = self.scale * (0.85 if pressed else 1.0)
        arrow = [(x + px * size, y + py * size) for px, py in _ARROW]
        draw.polygon([(px + 2, py + 3) for px, py in arrow], fill=(0, 0, 0, 70))  # shadow
        draw.polygon(arrow, fill=(255, 255, 255, 255), outline=(20, 20, 20, 255), width=2)

    def _draw_typing(self, draw: ImageDraw.ImageDraw, now: float) -> None:
        text = self._caption(now)
        if not text:
            return
        left, top, right, bottom = draw.textbbox((0, 0), text, font=self.font)
        pad_x, pad_y = 18, 12
        width, height = right - left + 2 * pad_x, bottom - top + 2 * pad_y
        x0, y0 = (self.size[0] - width) / 2, self.size[1] - height - 36
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
        while first and shown[first][1].at - shown[first - 1][1].at <= _TYPING_GAP_S:
            first -= 1
        burst = [e.text if e.kind == "input" else f" [{e.text}] " for _, e in shown[first:]]
        text = "".join(burst).strip()
        return text if len(text) <= _CAPTION_CHARS else "..." + text[-_CAPTION_CHARS:]


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


def _ease_out(k: float) -> float:
    return 1 - (1 - k) ** 3


def _fit(jpeg: bytes, size: tuple[int, int]) -> Image.Image:
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    if image.size == size:
        return image
    canvas = Image.new("RGB", size)
    image.thumbnail(size)
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas
