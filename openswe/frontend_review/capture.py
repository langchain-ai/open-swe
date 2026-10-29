"""Paired anonymous preview captures in a fresh, disposable LangSmith browser."""

import asyncio
import base64
import ipaddress
import json
import logging
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid7

import httpx2
from PIL import Image
from pydantic import JsonValue, TypeAdapter
from runtime_api.frontend_review import FrontendJourney
from websockets.asyncio.client import ClientConnection, connect

from openswe.sandboxes.browsers import LangSmithBrowser
from openswe.sandboxes.providers.langsmith_api import SandboxAPI

from .interactions import InteractionCapture, InteractionReplay
from .models import FrontendReviewRun
from .pixels import compare
from .schemas import FrontendReport, FrontendScenario
from .service import EvidenceAsset, finish_review

logger = logging.getLogger(__name__)
_object = TypeAdapter(dict[str, JsonValue])
_WIDTH = 1440
_HEIGHT = 1000
_PREVIEW_DOMAINS = ("smith.langchain.com", "vercel.app", "netlify.app", "pages.dev")


class CaptureBlockedError(RuntimeError):
    """A preview needs authentication or is outside the capture boundary."""


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    default_port = 443 if parsed.scheme in ("https", "wss") else 80
    return parsed.scheme, parsed.hostname, parsed.port or default_port


def validate_preview_url(url: str) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or not any(host == domain or host.endswith(f".{domain}") for domain in _PREVIEW_DOMAINS)
    ):
        raise CaptureBlockedError(
            "Anonymous capture supports HTTPS LangSmith, Vercel, Netlify, and Cloudflare previews"
        )


class _PublicResourceFilter:
    """Best-effort request filtering, never a browser network security boundary."""

    def __init__(self) -> None:
        self._public_hosts: dict[str, bool] = {}

    async def allows(self, url: str) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme in ("data", "blob", "about"):
            return True
        host = parsed.hostname or ""
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return False
        if host not in self._public_hosts:
            try:
                addresses = await asyncio.get_running_loop().getaddrinfo(
                    host, parsed.port or 443, type=socket.SOCK_STREAM
                )
                self._public_hosts[host] = bool(addresses) and all(
                    ipaddress.ip_address(address[4][0]).is_global for address in addresses
                )
            except OSError, ValueError:
                logger.info("Could not resolve public preview resource", extra={"host": host})
                self._public_hosts[host] = False
        return self._public_hosts[host]


class _CDP:
    """A sequential CDP connection that also handles paused network requests."""

    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection
        self._next_id = 0
        self._network = _PublicResourceFilter()
        self._document_status: dict[tuple[str, str], int] = {}

    async def _send(self, method: str, params: dict[str, JsonValue], session: str | None) -> int:
        self._next_id += 1
        body: dict[str, JsonValue] = {"id": self._next_id, "method": method, "params": params}
        if session is not None:
            body["sessionId"] = session
        await self._connection.send(json.dumps(body))
        return self._next_id

    async def call(
        self, method: str, params: dict[str, JsonValue] | None = None, *, session: str | None = None
    ) -> dict[str, JsonValue]:
        command = await self._send(method, params or {}, session)
        async with asyncio.timeout(45):
            while True:
                reply = _object.validate_json(await self._connection.recv())
                if reply.get("method") == "Fetch.requestPaused":
                    await self._release_request(reply)
                elif reply.get("method") == "Network.responseReceived":
                    self._record_response(reply)
                if reply.get("id") != command:
                    continue
                if "error" in reply:
                    raise RuntimeError(f"Preview browser rejected {method}")
                return _object.validate_python(reply.get("result", {}))

    def _record_response(self, event: dict[str, JsonValue]) -> None:
        params = _object.validate_python(event.get("params", {}))
        response = _object.validate_python(params.get("response", {}))
        session, frame, status = (
            event.get("sessionId"),
            params.get("frameId"),
            response.get("status"),
        )
        if (
            params.get("type") == "Document"
            and isinstance(session, str)
            and isinstance(frame, str)
            and isinstance(status, int)
            and 100 <= status <= 599
        ):
            self._document_status[(session, frame)] = status

    def _checked_document_status(self, session: str, frame: str) -> int | None:
        status = self._document_status.get((session, frame))
        if status in (401, 403):
            raise CaptureBlockedError(
                f"Preview denied anonymous access (HTTP {status}); a disposable QA login is required"
            )
        if status is not None and status >= 400:
            raise RuntimeError(f"Preview document returned HTTP {status}")
        return status

    async def _release_request(self, event: dict[str, JsonValue]) -> None:
        params = _object.validate_python(event.get("params", {}))
        request = _object.validate_python(params.get("request", {}))
        url = request.get("url")
        allowed = isinstance(url, str) and await self._network.allows(url)
        request_id = params.get("requestId")
        session = event.get("sessionId")
        await self._send(
            "Fetch.continueRequest" if allowed else "Fetch.failRequest",
            {"requestId": request_id, **({} if allowed else {"errorReason": "BlockedByClient"})},
            session if isinstance(session, str) else None,
        )

    async def evaluate(self, expression: str, session: str) -> JsonValue:
        reply = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            session=session,
        )
        if "exceptionDetails" in reply:
            raise RuntimeError("Preview readiness check failed")
        return _object.validate_python(reply.get("result", {})).get("value")

    async def screenshot(self, url: str) -> bytes:
        async with self.page(url) as session:
            shot = await self.call(
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": False},
                session=session,
            )
            data = shot.get("data")
            if not isinstance(data, str):
                raise RuntimeError("Preview browser returned no screenshot")
            return base64.b64decode(data, validate=True)

    async def interact(self, url: str, journey: FrontendJourney) -> list[InteractionCapture]:
        try:
            async with self.page(url) as session:
                return await InteractionReplay(self, session, url).capture(journey.steps)
        except (RuntimeError, TimeoutError) as exc:
            return [InteractionCapture(error=str(exc)) for s in journey.steps if s.checkpoint]

    @asynccontextmanager
    async def page(self, url: str) -> AsyncIterator[str]:
        context = await self.call("Target.createBrowserContext")
        context_id = context.get("browserContextId")
        if not isinstance(context_id, str):
            raise RuntimeError("Preview browser did not create an isolated context")
        created = await self.call(
            "Target.createTarget", {"url": "about:blank", "browserContextId": context_id}
        )
        target = created.get("targetId")
        if not isinstance(target, str):
            raise RuntimeError("Preview browser did not create a page")
        attached = await self.call("Target.attachToTarget", {"targetId": target, "flatten": True})
        session = attached.get("sessionId")
        if not isinstance(session, str):
            raise RuntimeError("Preview browser did not attach a page")
        try:
            await self.call("Page.enable", session=session)
            await self.call("Runtime.enable", session=session)
            await self.call("Network.enable", session=session)
            await self.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": _WIDTH, "height": _HEIGHT, "deviceScaleFactor": 1, "mobile": False},
                session=session,
            )
            await self.call(
                "Fetch.enable",
                {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
                session=session,
            )
            navigation = await self.call("Page.navigate", {"url": url}, session=session)
            if navigation.get("errorText"):
                raise RuntimeError("Preview page could not be reached")
            frame = navigation.get("frameId")
            if not isinstance(frame, str):
                raise RuntimeError("Preview browser did not identify the main document")
            async with asyncio.timeout(45):
                while True:
                    state = await self.evaluate("document.readyState", session)
                    status = self._checked_document_status(session, frame)
                    if state == "complete" and status is not None:
                        break
                    await asyncio.sleep(0.2)
            ready = await self.evaluate(
                "(async()=>{await document.fonts.ready; const s=document.createElement('style');"
                "s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;"
                "caret-color:transparent!important}'; document.head.appendChild(s);"
                "const visible=i=>{const r=i.getBoundingClientRect();return "
                "r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth};"
                "await Promise.all([...document.images].filter(i=>i.loading!=='lazy'||visible(i))"
                ".map(i=>i.complete?Promise.resolve():new Promise(resolve=>{"
                "const done=()=>{clearTimeout(timer);i.removeEventListener('load',done);"
                "i.removeEventListener('error',done);resolve()};"
                "const timer=setTimeout(done,3000);i.addEventListener('load',done,{once:true});"
                "i.addEventListener('error',done,{once:true})})));"
                "return await new Promise(resolve=>{let timer;const finish=ok=>{clearTimeout(timer);"
                "clearTimeout(deadline);observer.disconnect();resolve(ok)};"
                "const settle=()=>{clearTimeout(timer);timer=setTimeout(()=>finish(true),750)};"
                "const observer=new MutationObserver(settle);"
                "const deadline=setTimeout(()=>finish(false),10000);"
                "observer.observe(document.body,{childList:true,subtree:true,attributes:true,"
                "characterData:true});settle()})})()",
                session,
            )
            if ready is not True:
                raise RuntimeError("Preview did not reach a stable rendered state")
            location = await self.evaluate("location.href", session)
            if not isinstance(location, str) or _origin(location) != _origin(url):
                raise CaptureBlockedError("Preview redirected away from its approved origin")
            auth = await self.evaluate(
                "Boolean(document.querySelector('input[type=password]')) || "
                "/\\/(login|signin|sign-in|auth)(\\/|$)/i.test(location.pathname) || "
                "/sign in to langsmith|log in to langsmith/i.test(document.body.innerText)",
                session,
            )
            if auth is True:
                raise CaptureBlockedError(
                    "Preview requires a disposable QA login; none is configured"
                )
            if self._checked_document_status(session, frame) is None:
                raise RuntimeError("Preview main document response is unavailable")
            yield session
        finally:
            self._document_status = {
                key: status for key, status in self._document_status.items() if key[0] != session
            }
            await self.call("Target.disposeBrowserContext", {"browserContextId": context_id})


@asynccontextmanager
async def _browser(name: str) -> AsyncIterator[_CDP]:
    _require_browser_network_isolation()
    browser = LangSmithBrowser(name)
    api = SandboxAPI.from_env()
    try:
        window = await browser.open_window("frontend-review", hidpi=False, idle_ttl_seconds=180)
        cdp = window.api_cdp_url
        api_origin = _origin(api.api_root)
        if not cdp or _origin(cdp) != api_origin:
            raise RuntimeError("Browser returned an untrusted connection origin")
        async with httpx2.AsyncClient(headers=api.headers(), timeout=30) as client:
            version = await client.get(f"{cdp.rstrip('/')}/json/version")
            version.raise_for_status()
        websocket = _object.validate_python(version.json()).get("webSocketDebuggerUrl")
        if (
            not isinstance(websocket, str)
            or urlsplit(websocket).scheme != "wss"
            or _origin(websocket)[1:] != api_origin[1:]
        ):
            raise RuntimeError("Browser returned an untrusted websocket origin")
        async with connect(
            websocket, additional_headers=api.headers(), max_size=20_000_000
        ) as socket:
            yield _CDP(socket)
    finally:
        try:
            await browser.delete()
        except Exception:
            logger.warning("Could not delete disposable frontend review browser", exc_info=True)


def _require_browser_network_isolation() -> None:
    # The provider's external egress proxy does not fence Chrome's own loopback services.
    # CDP interception also misses workers, popups and WebSockets, and cannot pin browser DNS.
    raise CaptureBlockedError(
        "Live preview capture is disabled until the browser service enforces network isolation "
        "for all browser traffic, including loopback services, workers, popups, and WebSockets"
    )


async def capture_run(run: FrontendReviewRun) -> None:
    """Capture the frozen request and persist a terminal report before returning."""
    status: Literal["complete", "blocked", "failed"] = "failed"
    error: str | None = None
    report: FrontendReport | None = None
    assets: list[EvidenceAsset] = []
    try:
        validate_preview_url(run.base_url)
        validate_preview_url(run.head_url)
        scenarios: list[FrontendScenario] = []
        async with asyncio.timeout(600), _browser(f"frontend-review-{uuid7().hex}") as browser:
            for index, path in enumerate(run.paths):
                before = await browser.screenshot(f"{run.base_url}{path}")
                after = await browser.screenshot(f"{run.head_url}{path}")
                pixels = await asyncio.to_thread(compare, before, after)
                names = {
                    kind: f"page-{index}-{kind}.png" for kind in ("before", "after", "diff", "mask")
                }
                assets.extend(
                    EvidenceAsset(name=names[kind], content_type="image/png", content=content)
                    for kind, content in (
                        ("before", before),
                        ("after", after),
                        ("diff", pixels.difference),
                        ("mask", pixels.mask),
                    )
                )
                scenarios.append(
                    FrontendScenario(
                        id=f"page-{index}",
                        title=path,
                        path=path,
                        status="complete",
                        before_url=names["before"],
                        after_url=names["after"],
                        diff_url=names["diff"],
                        mask_url=names["mask"],
                        width=pixels.width,
                        height=pixels.height,
                        metrics=pixels.metrics,
                        regions=pixels.regions,
                    )
                )
            for journey_index, raw in enumerate(run.journeys):
                journey = FrontendJourney.model_validate(raw)
                before_states = await browser.interact(f"{run.base_url}{journey.path}", journey)
                after_states = await browser.interact(f"{run.head_url}{journey.path}", journey)
                actions: list[str] = []
                checkpoint = 0
                for step in journey.steps:
                    actions.append(f"{step.action} {step.selector}")
                    if not step.checkpoint:
                        continue
                    before_state, after_state = before_states[checkpoint], after_states[checkpoint]
                    scenario_id = f"journey-{journey_index}-{checkpoint}"
                    scenario = FrontendScenario(
                        id=scenario_id,
                        title=f"{journey.title} · {step.checkpoint}",
                        path=journey.path,
                        status="blocked",
                        interaction=True,
                        actions=list(actions),
                        before_error=before_state.error,
                        after_error=after_state.error,
                        layout_warnings=[
                            *(f"Base: {w}" for w in before_state.warnings),
                            *(f"PR: {w}" for w in after_state.warnings),
                        ],
                    )
                    for role, state in (("before", before_state), ("after", after_state)):
                        if state.image is not None:
                            with Image.open(BytesIO(state.image)) as image:
                                scenario.width, scenario.height = image.size
                            name = f"{scenario_id}-{role}.png"
                            setattr(scenario, f"{role}_url", name)
                            assets.append(
                                EvidenceAsset(
                                    name=name, content_type="image/png", content=state.image
                                )
                            )
                    if before_state.image and after_state.image:
                        pixels = await asyncio.to_thread(
                            compare, before_state.image, after_state.image
                        )
                        scenario.status = "complete"
                        scenario.metrics, scenario.regions = pixels.metrics, pixels.regions
                        for kind, content in (("diff", pixels.difference), ("mask", pixels.mask)):
                            name = f"{scenario_id}-{kind}.png"
                            setattr(scenario, f"{kind}_url", name)
                            assets.append(
                                EvidenceAsset(name=name, content_type="image/png", content=content)
                            )
                    scenarios.append(scenario)
                    checkpoint += 1
        report = FrontendReport(
            summary=(
                f"Captured {len(run.paths)} paired routes and "
                f"{sum(s.interaction and s.status == 'complete' for s in scenarios)} paired interaction states. "
                "Missing interaction states remain explicitly incomplete. Pixel highlights show "
                "visual changes; AI design review has not run. Preview builds are not attested "
                "to the recorded Git revisions. Video recording is unavailable."
            ),
            scenarios=scenarios,
        )
        status = "complete"
    except CaptureBlockedError as exc:
        status, error = "blocked", str(exc)
    except Exception:
        logger.exception(
            "Frontend review capture failed", extra={"frontend_review_id": str(run.id)}
        )
        error = "Capture failed. Check browser availability and whether the preview is reachable."
    await finish_review(
        run.id,
        owner=run.owner,
        repo=run.repo,
        pr_number=run.pr_number,
        status=status,
        report=report,
        error=error,
        assets=assets if status == "complete" else [],
    )
