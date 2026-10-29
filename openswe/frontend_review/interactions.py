"""Bounded interaction checkpoints replayed identically on both preview revisions."""

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import JsonValue
from runtime_api.frontend_review import FrontendInteractionStep


class InteractionBrowser(Protocol):
    async def evaluate(self, expression: str, session: str) -> JsonValue: ...

    async def call(
        self, method: str, params: dict[str, JsonValue] | None = None, *, session: str | None = None
    ) -> dict[str, JsonValue]: ...


@dataclass
class InteractionCapture:
    image: bytes | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


_TARGET = """(selector => {
 const matches = document.querySelectorAll(selector);
 if (matches.length !== 1) return null;
 const el = matches[0];
 el.scrollIntoView({block:'center', inline:'nearest', behavior:'instant'});
 const r = el.getBoundingClientRect(), style = getComputedStyle(el);
 if (!r.width || !r.height || style.visibility !== 'visible' || style.display === 'none' ||
     el.matches(':disabled,[aria-disabled="true"]') || el.closest('[inert]')) return null;
 const x = Math.max(0, r.left) + (Math.min(innerWidth, r.right) - Math.max(0, r.left))/2;
 const y = Math.max(0, r.top) + (Math.min(innerHeight, r.bottom) - Math.max(0, r.top))/2;
 const hit = document.elementFromPoint(x,y);
 if (!hit || (hit !== el && !el.contains(hit))) return null;
 return {x,y};
})"""

_SETTLE = """new Promise(resolve => {
 let timer;
 const finish = ok => {clearTimeout(timer); clearTimeout(deadline); observer.disconnect(); resolve(ok)};
 const settle = () => {clearTimeout(timer); timer = setTimeout(() => finish(true), 350)};
 const observer = new MutationObserver(settle);
 const deadline = setTimeout(() => finish(false), 5000);
 observer.observe(document.body, {childList:true, subtree:true, attributes:true, characterData:true});
 settle();
})"""

_LAYOUT = """(selector => {
 const root = document.querySelector(selector), warnings = [];
 const visible = el => {const r=el.getBoundingClientRect(); return r.width>0 && r.height>0 &&
   getComputedStyle(el).visibility==='visible'};
 if (!root || !visible(root)) return [];
 const dialog = root.matches('[role=dialog],dialog') ? root : root.closest('[role=dialog],dialog');
 if (!dialog) return [];
 const r = dialog.getBoundingClientRect();
 if (r.left < -1 || r.top < -1 || r.right > innerWidth+1 || r.bottom > innerHeight+1)
   warnings.push('Dialog bounds extend beyond the captured viewport; inspect its scrolling and close controls.');
 for (const el of [dialog,...dialog.querySelectorAll('button,input,textarea,select,[role=button]')]) {
   if (!visible(el)) continue;
   const rect=el.getBoundingClientRect();
   for (let parent=el.parentElement; parent; parent=parent.parentElement) {
     const style=getComputedStyle(parent), p=parent.getBoundingClientRect();
     const clippedX=/^(hidden|clip)$/.test(style.overflowX) && (rect.left<p.left-1 || rect.right>p.right+1);
     const clippedY=/^(hidden|clip)$/.test(style.overflowY) && (rect.top<p.top-1 || rect.bottom>p.bottom+1);
     if (clippedX || clippedY) {warnings.push('Dialog or one of its controls may be clipped by an overflow container.'); break;}
     if (parent===dialog) break;
   }
 }
 return [...new Set(warnings)].slice(0,5);
})"""


class InteractionReplay:
    def __init__(self, browser: InteractionBrowser, session: str, url: str) -> None:
        self.browser = browser
        self.session = session
        self.origin = urlsplit(url)

    async def _origin_unchanged(self) -> None:
        location = await self.browser.evaluate("location.href", self.session)
        if not isinstance(location, str):
            raise RuntimeError("The interaction page location is unavailable")
        parsed = urlsplit(location)
        if (parsed.scheme, parsed.hostname, parsed.port or 443) != (
            self.origin.scheme,
            self.origin.hostname,
            self.origin.port or 443,
        ):
            raise RuntimeError("Interaction left the approved preview origin")
        if (
            await self.browser.evaluate(
                "Boolean(document.querySelector('input[type=password]')) || "
                "/\\/(login|signin|sign-in|auth)(\\/|$)/i.test(location.pathname)",
                self.session,
            )
            is True
        ):
            raise RuntimeError(
                "Interaction reached authentication; a disposable QA identity is required"
            )

    async def _mouse(self, kind: str, point: dict[str, JsonValue], *, down: bool = False) -> None:
        await self.browser.call(
            "Input.dispatchMouseEvent",
            {
                "type": kind,
                "x": point["x"],
                "y": point["y"],
                "button": "left" if kind != "mouseMoved" else "none",
                "buttons": 1 if down else 0,
                **({"clickCount": 1} if kind != "mouseMoved" else {}),
            },
            session=self.session,
        )

    async def _key(self, key: str, code: str, keycode: int) -> None:
        for kind in ("keyDown", "keyUp"):
            await self.browser.call(
                "Input.dispatchKeyEvent",
                {"type": kind, "key": key, "code": code, "windowsVirtualKeyCode": keycode},
                session=self.session,
            )

    async def capture(self, steps: list[FrontendInteractionStep]) -> list[InteractionCapture]:
        captures: list[InteractionCapture] = []
        failure: str | None = None
        for step in steps:
            if failure is not None:
                if step.checkpoint:
                    captures.append(
                        InteractionCapture(error=f"Earlier step blocked this state: {failure}")
                    )
                continue
            point: dict[str, JsonValue] | None = None
            held = False
            try:
                await self._origin_unchanged()
                target = await self.browser.evaluate(
                    f"{_TARGET}({json.dumps(step.selector)})", self.session
                )
                if not isinstance(target, dict) or not all(
                    isinstance(target.get(k), (float, int)) for k in ("x", "y")
                ):
                    raise RuntimeError(
                        f"{step.action}: target is missing, ambiguous, disabled, or obscured"
                    )
                point = target
                if step.action in {"hover", "press", "click"}:
                    await self._mouse("mouseMoved", point)
                    if step.action in {"press", "click"}:
                        await self._mouse("mousePressed", point, down=True)
                        held = True
                    if step.action == "click":
                        await self._mouse("mouseReleased", point)
                        held = False
                elif step.action in {"focus", "type"}:
                    if step.action == "focus":
                        await self._key("Shift", "ShiftLeft", 16)
                    selector = json.dumps(step.selector)
                    focused = await self.browser.evaluate(
                        f"(()=>{{const el=document.querySelector({selector});"
                        "if (el.matches('input[type=password],input[type=file],:disabled,[readonly]')) return false;"
                        + (
                            "if (!el.matches('input:not([type]),input[type=text],input[type=search],input[type=url],textarea')) return false;"
                            if step.action == "type"
                            else ""
                        )
                        + "el.focus();"
                        + ("el.select();" if step.action == "type" else "")
                        + "return document.activeElement===el})()",
                        self.session,
                    )
                    if focused is not True:
                        raise RuntimeError(
                            "Target cannot receive the requested focus or synthetic text"
                        )
                    if step.action == "type":
                        await self.browser.call(
                            "Input.insertText", {"text": step.text}, session=self.session
                        )
                        if (
                            await self.browser.evaluate(
                                f"document.querySelector({selector}).value === {json.dumps(step.text)}",
                                self.session,
                            )
                            is not True
                        ):
                            raise RuntimeError("Input did not retain the synthetic text")
                else:
                    await self._key("Escape", "Escape", 27)
                if await self.browser.evaluate(_SETTLE, self.session) is not True:
                    raise RuntimeError("Interaction did not reach a stable rendered state")
                await self._origin_unchanged()
                if step.expect_visible:
                    async with asyncio.timeout(5):
                        while not await self.browser.evaluate(
                            "(s=>{const matches=document.querySelectorAll(s);if(matches.length!==1)return false;const e=matches[0];"
                            "const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&"
                            "getComputedStyle(e).visibility==='visible'})"
                            f"({json.dumps(step.expect_visible)})",
                            self.session,
                        ):
                            await asyncio.sleep(0.1)
                    if await self.browser.evaluate(_SETTLE, self.session) is not True:
                        raise RuntimeError("Expected content did not settle")
                if step.checkpoint:
                    warnings = await self.browser.evaluate(
                        f"{_LAYOUT}({json.dumps(step.expect_visible or step.selector)})",
                        self.session,
                    )
                    shot = await self.browser.call(
                        "Page.captureScreenshot",
                        {"format": "png", "captureBeyondViewport": False},
                        session=self.session,
                    )
                    data = shot.get("data")
                    if not isinstance(data, str):
                        raise RuntimeError("Interaction screenshot is unavailable")
                    captures.append(
                        InteractionCapture(
                            image=base64.b64decode(data, validate=True),
                            warnings=[w for w in warnings if isinstance(w, str)]
                            if isinstance(warnings, list)
                            else [],
                        )
                    )
            except (RuntimeError, TimeoutError, ValueError) as exc:
                failure = str(exc) or "Expected content did not appear within five seconds"
                if step.checkpoint:
                    captures.append(InteractionCapture(error=failure))
            finally:
                if held and point is not None:
                    # Release away from the control so capturing :active does not also click it.
                    await self._mouse("mouseMoved", {"x": -1, "y": -1}, down=True)
                    await self._mouse("mouseReleased", {"x": -1, "y": -1})
        return captures
