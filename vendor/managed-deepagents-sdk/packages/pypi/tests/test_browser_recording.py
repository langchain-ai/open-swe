"""Browser recordings: the recorder program, against a fake Chrome, with no browser."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import time
from pathlib import Path
from typing import Any, Self, cast

import av
import pytest
import websockets
from PIL import Image

from managed_deepagents import _browser_recording as rec


def _jpeg(color: str, size: tuple[int, int] = (320, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


def _frame(at: float, color: str = "white") -> rec.Frame:
    return rec.Frame(at, _jpeg(color), 320, 200)


def _decode(video: bytes) -> list[Image.Image]:
    with av.open(io.BytesIO(video), mode="r") as container:
        return [frame.to_image() for frame in container.decode(video=0)]


def _rgb(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    return cast("tuple[int, int, int]", image.getpixel((x, y)))


def test_a_recordings_pauses_are_cut_short() -> None:
    # 0.5s of motion, then the model thinks for a minute, then a last change.
    frames = [_frame(0), _frame(0.5), _frame(60.5)]
    clock = rec.Clock(rec.Take(frames=frames))
    assert clock.frame_starts == [0, 0.5, 0.5 + rec.MAX_HOLD_S]
    assert clock.length == 0.5 + rec.MAX_HOLD_S + rec.END_HOLD_S


def test_a_burst_of_input_is_spread_out_to_read() -> None:
    # An agent clicked, typed and submitted in one call, within 30ms.
    events = [
        rec.Event(1.00, "click", 10, 10),
        rec.Event(1.01, "input", text="tomsmith"),
        rec.Event(1.02, "click", 10, 40),
        rec.Event(1.03, "key", text="Enter"),
    ]
    clock = rec.Clock(rec.Take(frames=[_frame(0), _frame(1.05)], events=events))
    times = [round(clock.of(event), 2) for event in events]
    assert times == [1.0, 1.7, 2.26, 2.96]  # a click 0.7s, 8 characters 0.56s, then Enter
    assert clock.frame_starts[1] == 3.31  # what the burst led to shows once it has played


def _count(image: Image.Image, match: Any) -> int:
    return sum(1 for pixel in image.convert("RGB").get_flattened_data() if match(pixel))


def _accent(pixel: tuple[int, int, int]) -> bool:
    r, _, b_ = pixel
    return b_ > 150 and r < 120


def test_a_recording_encodes_with_a_cursor_that_glides_to_each_click() -> None:
    take = rec.Take(
        frames=[_frame(0, "black"), _frame(1, "black")],
        events=[rec.Event(1.0, "click", 250, 150, target='button "Save"')],
    )
    images = _decode(rec.encode(take))

    assert len(images) == round(rec.Clock(take).length * rec.FPS)
    assert images[0].size == (320, 200)
    assert min(_rgb(images[0], 162, 110)) > 200, "the cursor starts mid-page"
    assert min(_rgb(images[rec.FPS], 252, 160)) > 200, "and has reached the click as it lands"
    assert _count(images[rec.FPS + 3], _accent) > 20, "where a ripple marks it"
    assert _count(images[-1], _accent) == 0, "which fades"


def test_a_recording_shows_what_is_typed() -> None:
    take = rec.Take(
        frames=[_frame(0), _frame(3)],
        events=[
            rec.Event(1.0, "input", text="Lang", target="input#q"),
            rec.Event(1.1, "input", text="Chain", target="input#q"),
            rec.Event(1.2, "key", text="Enter", target="input#q"),
        ],
    )
    images = _decode(rec.encode(take))

    def pill(image: Image.Image) -> int:  # dark pixels along the bottom
        return _count(image.crop((0, 140, 320, 200)), lambda px: max(px) < 60)

    assert pill(images[0]) == 0
    assert pill(images[int(1.3 * rec.FPS)]) > 200, "the typing shows as a caption"
    assert pill(images[-1]) == 0, "until a while after the last key"


def test_a_recording_at_twice_the_pages_size_draws_its_overlay_at_that_size() -> None:
    def drawn(scale: int) -> tuple[int, int]:
        """The most ripple and caption pixels in any frame, on a gray 320x200 page."""
        size = (320 * scale, 200 * scale)
        take = rec.Take(
            frames=[rec.Frame(t, _jpeg("gray", size), 320, 200) for t in (0, 4)],
            events=[
                rec.Event(1.0, "click", 160, 60),
                rec.Event(2.5, "input", text="LangChain", target="input#q"),
            ],
        )
        images = _decode(rec.encode(take))
        ripple = max(_count(image, _accent) for image in images)
        bottom = (0, 140 * scale, 320 * scale, 200 * scale)
        caption = max(_count(i.crop(bottom), lambda px: max(px) < 60) for i in images)
        return ripple, caption

    (ripple, caption), (ripple2, caption2) = drawn(1), drawn(2)
    assert ripple2 > 3 * ripple, "a click's ripple covers the same part of the page"
    assert caption2 > 3 * caption, "and so does the typing's caption"


def test_a_recordings_events_are_on_the_videos_clock() -> None:
    # Typing gets time to read, and a minute of the model thinking is cut to MAX_HOLD_S.
    take = rec.Take(
        frames=[_frame(0), _frame(60)],
        events=[
            rec.Event(0, "navigate", text="https://example.com/"),
            rec.Event(0.1, "move", 10, 10),
            rec.Event(0.2, "input", text="Lang", target="input#q"),
            rec.Event(0.3, "input", text="Chain", target="input#q"),
            rec.Event(0.4, "key", text="Enter", target="input#q"),
            rec.Event(60, "click", 12.4, 30.6, target='a "Docs"'),
            rec.Event(60, "navigate", text="https://example.com/"),  # the same page
        ],
    )
    assert rec.events(take) == [
        {"t": 0, "type": "navigate", "url": "https://example.com/"},
        {"t": 0.2, "type": "type", "text": "LangChain", "target": "input#q"},
        {"t": 0.83, "type": "key", "key": "Enter", "target": "input#q"},
        {"t": 2.33, "type": "click", "x": 12, "y": 31, "target": 'a "Docs"'},
    ]


def test_a_recording_with_frames_of_another_size_still_encodes() -> None:
    other = rec.Frame(1, _jpeg("black", (640, 480)), 640, 480)
    images = _decode(rec.encode(rec.Take(frames=[_frame(0), other])))
    assert {image.size for image in images} == {(320, 200)}


def test_a_recording_with_no_frames_is_an_error() -> None:
    with pytest.raises(ValueError, match="no frames"):
        rec.encode(rec.Take())


def test_frames_that_arrive_out_of_order_play_in_the_order_chrome_painted_them(
    tmp_path: Path,
) -> None:
    # Chrome painted white, red, then black; the black frame arrived before the red one.
    rec._save(tmp_path, rec.Take(frames=[_frame(0), _frame(2, "black"), _frame(1, "red")]))
    painted = {"white": (255, 255, 255), "red": (255, 0, 0), "black": (0, 0, 0)}

    def shows(image: Image.Image) -> str:
        pixel = _rgb(image.convert("RGB"), 40, 40)  # away from the cursor
        return min(painted, key=lambda name: math.dist(pixel, painted[name]))

    images = _decode((tmp_path / "take.mp4").read_bytes())
    assert [shows(images[int(t * rec.FPS)]) for t in (0.5, 1.5, 2.5)] == ["white", "red", "black"]
    assert shows(Image.open(tmp_path / "first.jpg")) == "white"
    assert shows(Image.open(tmp_path / "last.jpg")) == "black"


def test_typing_reads_once_the_page_shows_it() -> None:
    # The agent fills a field, the page shows it 20ms later, and it clicks on.
    events = [
        rec.Event(1.00, "input", text="tomsmith", target="input#user"),
        rec.Event(1.05, "click", 10, 40),
    ]
    clock = rec.Clock(rec.Take(frames=[_frame(0), _frame(1.02)], events=events))
    assert clock.frame_starts[1] == pytest.approx(1.02), "the caption and the page show it at once"
    assert clock.of(events[1]) == pytest.approx(1.02 + 8 * 0.07), "then it has time to read"


def test_typing_into_another_field_is_another_burst_and_a_password_one_mask() -> None:
    take = rec.Take(
        frames=[_frame(0), _frame(3)],
        events=[
            rec.Event(1.0, "input", text="Ada", target="input#name"),
            rec.Event(1.1, "input", text=rec._MASK, target="input#pw"),
            rec.Event(1.2, "input", text=rec._MASK, target="input#pw"),
        ],
    )
    typed = [(event["text"], event["target"]) for event in rec.events(take)]
    assert typed == [("Ada", "input#name"), (rec._MASK, "input#pw")]
    overlay = rec._Overlay(take, (320, 200))
    assert overlay._caption(overlay.clock.of(take.events[-1])) == rec._MASK


def test_input_is_timed_by_when_it_happened_in_the_page() -> None:
    # The frame that shows typing arrives after the click that follows it, but was
    # painted before; Chrome's times, on the box's clock, keep them in order.
    now = 1_800_000_000.0
    typed = rec._event(now, {"type": "input", "text": "a", "at": (now - 0.05) * 1000})
    assert typed.at == pytest.approx(now - 0.05)
    elsewhere = rec._event(now, {"type": "input", "text": "a", "at": 12.5})  # another clock
    assert elsewhere.at == now


def test_recorded_urls_lose_what_can_carry_a_secret() -> None:
    assert (
        rec._bare("https://ada:pw@acme.test/done.html?pw=hunter2#t")
        == "https://acme.test/done.html"
    )
    assert rec._bare("data:text/html,<p>token=1</p>") == "data:text/html,..."


def test_a_recordings_summary_lists_at_most_30_events_with_typing_quoted() -> None:
    key = {"t": 9, "type": "key", "key": "Tab", "target": "input#a"}
    happened: list[dict[str, object]] = [
        {"t": 1, "type": "type", "text": "it's", "target": "input#a"},
        {"t": 2, "type": "type", "text": 'say "hi"\n', "target": ""},
        *[key] * 30,
    ]
    lines = rec.summary(happened).split("\n")
    assert lines[:3] == [
        """   1.0s typed "it's" into input#a""",
        """   2.0s typed 'say "hi"\\n' into the page""",
        "   9.0s pressed Tab",
    ]
    assert len(lines) == 31
    assert lines[-1] == "... and 2 more in the JSON."
    assert rec.summary([]) == "(nothing)"


class _Chrome:
    """A window's CDP endpoint, as far as the recorder uses it: tabs that each show a color.

    A tab paints one screencast frame when the recorder starts casting it; a later
    color shows only in a screenshot, as when the last action's frame never came.
    Its tabs are 320x200 CSS pixels, painted at ``scale`` device pixels to each, and
    each frame is fitted within the screencast's ``maxWidth`` and ``maxHeight``. It
    will not attach to the tabs in ``refused``, and a session ends when detached.
    """

    def __init__(self, scale: int = 1) -> None:
        self.scale = scale
        self.colors = {"tab0": "white"}
        self.attached: list[str] = []
        self.refused: set[str] = set()
        self.casting: list[str] = []  # tabs, as their screencasts start
        self._cast = asyncio.Condition()
        self._tabs: dict[str, str] = {}  # by session
        self._sockets: set[Any] = set()
        self._server: Any = None

    @property
    def url(self) -> str:
        port = self._server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/devtools/browser/w1"

    async def __aenter__(self) -> Self:
        self._server = await websockets.serve(self._serve, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, ws: Any) -> None:
        self._sockets.add(ws)
        try:
            async for raw in ws:
                message = json.loads(raw)
                method, params = message["method"], message.get("params", {})
                tab = self._tabs.get(message.get("sessionId", ""), "")
                try:
                    result = self._answer(method, params, tab)
                except LookupError as err:  # a tab or session it does not have
                    await ws.send(json.dumps({"id": message["id"], "error": {"message": str(err)}}))
                    async with self._cast:
                        self._cast.notify_all()
                    continue
                await ws.send(json.dumps({"id": message["id"], "result": result}))
                if method == "Target.setDiscoverTargets":  # Chrome tells of the tabs it has
                    for known in list(self.colors):
                        await self._created(ws, known)
                elif method == "Page.startScreencast":
                    async with self._cast:
                        self.casting.append(tab)
                        self._cast.notify_all()
                    frame = {"deviceWidth": 320, "deviceHeight": 200, "timestamp": time.time()}
                    data = base64.b64encode(_jpeg(self.colors[tab], self._painted(params))).decode()
                    cast = {"data": data, "sessionId": 1, "metadata": frame}
                    await self._send(ws, "Page.screencastFrame", cast, message["sessionId"])
        finally:
            self._sockets.discard(ws)

    def _painted(self, cast: dict[str, Any]) -> tuple[int, int]:
        width, height = 320 * self.scale, 200 * self.scale
        fit = min(1.0, cast.get("maxWidth", width) / width, cast.get("maxHeight", height) / height)
        return round(width * fit), round(height * fit)

    def _answer(self, method: str, params: dict[str, Any], tab: str) -> dict[str, Any]:
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": t, "type": "page"} for t in self.colors]}
        if method == "Target.attachToTarget":
            self.attached.append(params["targetId"])
            if params["targetId"] in self.refused:
                raise LookupError(params["targetId"])
            session = f"session-{len(self.attached)}"
            self._tabs[session] = params["targetId"]
            return {"sessionId": session}
        if method == "Target.detachFromTarget":
            del self._tabs[params["sessionId"]]
        if method == "Page.getFrameTree":
            url = f"https://acme.test/{tab}.html?pw=hunter2#top"
            return {"frameTree": {"frame": {"id": "frame", "url": url}}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 1}
        if method == "Page.captureScreenshot":  # at the HiDPI window's own scale
            return {"data": base64.b64encode(_jpeg(self.colors[tab], (640, 400))).decode()}
        return {}

    async def _send(self, ws: Any, method: str, params: object, session: str = "") -> None:
        await ws.send(json.dumps({"method": method, "params": params, "sessionId": session}))

    async def _created(self, ws: Any, tab: str) -> None:
        await self._send(
            ws, "Target.targetCreated", {"targetInfo": {"targetId": tab, "type": "page"}}
        )

    async def open_tab(self, tab: str, color: str) -> None:
        self.colors[tab] = color
        for ws in self._sockets:
            await self._created(ws, tab)
        async with asyncio.timeout(10), self._cast:
            await self._cast.wait_for(
                lambda: self.casting[-1:] == [tab] or (tab in self.refused and tab in self.attached)
            )

    async def close(self) -> None:
        """Chrome goes away mid-take, as when its window closes."""
        for ws in list(self._sockets):
            await ws.close()

    async def type(self, text: str, target: str) -> None:
        """Typing in the tab being recorded, as the recorder's script reports it."""
        session = next(reversed(self._tabs))
        payload = json.dumps({"type": "input", "text": text, "target": target})
        for ws in self._sockets:
            await self._send(
                ws, "Runtime.bindingCalled", {"name": rec._BINDING, "payload": payload}, session
            )


def test_a_take_ends_once_its_frames_fill_the_memory_they_may_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "_MAX_FRAME_BYTES", 1)  # the first frame fills it

    async def go() -> dict[str, object]:
        async with _Chrome() as chrome, asyncio.timeout(10):  # not MAX_S
            await rec._record(tmp_path, chrome.url, "tab0")
        return json.loads((tmp_path / "result.json").read_text())

    assert asyncio.run(go())["early"] == (
        "It stopped early, at the recorder's limit: 5 minutes, or less"
        " on a page that kept changing."
    )
    assert _decode((tmp_path / "take.mp4").read_bytes())


def test_a_take_ends_at_the_limit_and_keeps_a_hidpi_tabs_device_pixels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "VIEWPORT", (320, 200))
    monkeypatch.setattr(rec, "MAX_S", 0.5)  # of 5 minutes

    async def go() -> None:
        async with _Chrome(scale=2) as chrome:
            await rec._record(tmp_path, chrome.url, "tab0")  # no one stops it

    asyncio.run(go())
    assert "at the recorder's limit" in json.loads((tmp_path / "result.json").read_text())["early"]
    assert {image.size for image in _decode((tmp_path / "take.mp4").read_bytes())} == {(640, 400)}
    # The frames the model checks stay at the page's own size.
    assert Image.open(tmp_path / "first.jpg").size == (320, 200)
    assert Image.open(tmp_path / "last.jpg").size == (320, 200)
