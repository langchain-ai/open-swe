"""Paired anonymous preview captures in a fresh, disposable LangSmith browser."""

import asyncio
import base64
import ipaddress
import json
import logging
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

import httpx2
from pydantic import JsonValue, TypeAdapter
from websockets.asyncio.client import ClientConnection, connect

from openswe.sandboxes.providers.langsmith_api import SandboxAPI

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


class _NetworkBoundary:
    """Only public HTTP resources; no local browser services or private network targets."""

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
        self._network = _NetworkBoundary()
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
            if not isinstance(location, str) or urlsplit(location).netloc != urlsplit(url).netloc:
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
            shot = await self.call(
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": False},
                session=session,
            )
            data = shot.get("data")
            if not isinstance(data, str):
                raise RuntimeError("Preview browser returned no screenshot")
            return base64.b64decode(data, validate=True)
        finally:
            self._document_status = {
                key: status for key, status in self._document_status.items() if key[0] != session
            }
            await self.call("Target.disposeBrowserContext", {"browserContextId": context_id})


@asynccontextmanager
async def _browser(name: str) -> AsyncIterator[_CDP]:
    api = SandboxAPI.from_env()
    created = False
    try:
        await api.request(
            "POST",
            "/browsers",
            json={"name": name, "idle_ttl_seconds": 180, "hidpi": False},
            timeout=180,
        )
        created = True
        window = await api.request(
            "POST",
            f"/browsers/{name}/attachments",
            json={"name": "frontend-review", "controller": "agent"},
            timeout=180,
        )
        payload = _object.validate_python(window.json())
        cdp = payload.get("api_cdp_url")
        if not isinstance(cdp, str) or urlsplit(cdp).netloc != urlsplit(api.api_root).netloc:
            raise RuntimeError("Browser returned an untrusted connection origin")
        async with httpx2.AsyncClient(headers=api.headers(), timeout=30) as client:
            version = await client.get(f"{cdp.rstrip('/')}/json/version")
            version.raise_for_status()
        websocket = _object.validate_python(version.json()).get("webSocketDebuggerUrl")
        if (
            not isinstance(websocket, str)
            or urlsplit(websocket).scheme != "wss"
            or urlsplit(websocket).netloc != urlsplit(api.api_root).netloc
        ):
            raise RuntimeError("Browser returned an untrusted websocket origin")
        async with connect(
            websocket, additional_headers=api.headers(), max_size=20_000_000
        ) as socket:
            yield _CDP(socket)
    finally:
        if created:
            try:
                await api.request("DELETE", f"/boxes/{name}", timeout=30)
            except Exception:
                logger.warning("Could not delete disposable frontend review browser", exc_info=True)


async def capture_run(run: FrontendReviewRun) -> None:
    """Capture the frozen request and persist a terminal report before returning."""
    status: Literal["complete", "blocked", "failed"] = "failed"
    error: str | None = None
    report: FrontendReport | None = None
    assets: list[EvidenceAsset] = []
    try:
        if run.auth_mode != "none":
            raise CaptureBlockedError(
                "Dummy authentication blocks capture before opening a browser"
            )
        validate_preview_url(run.base_url)
        validate_preview_url(run.head_url)
        scenarios: list[FrontendScenario] = []
        async with asyncio.timeout(600), _browser(f"frontend-review-{run.id.hex}") as browser:
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
        report = FrontendReport(
            summary=(
                f"Captured {len(scenarios)} paired routes anonymously. Pixel highlights show "
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
