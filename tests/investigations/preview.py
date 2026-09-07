"""Isolated Investigate demo. Run: .venv/bin/python -m tests.investigations.preview.

Only this test harness installs fake provider boundaries. Production routers,
receipt acceptance, coordinator, TypedStore validation, and workers stay real.
The existing Vite server on port 3000 supplies UI assets; all demo state is lost
when this process exits. No environment or credential file is loaded.
"""

import asyncio
import html
import time
from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agent import store as agent_store
from agent.dashboard import investigate_api
from agent.dashboard.oauth import require_session
from agent.investigations import coordinator, service, slack, worker
from agent.investigations.models import (
    Evidence,
    Hypothesis,
    InvestigationPolicy,
    InvestigationReport,
)
from agent.utils import dashboard_ui
from tests.conftest import FakeStore

BASE_URL = "http://127.0.0.1:2025"
VITE_URL = "http://localhost:3000"
BANNER_TEXT = "Local demo · synthetic incident data · no live integrations"
WORKSPACE_ID = "TDEMO"
APP_ID = "ADEMO"
CHANNEL_ID = "CDEMO001"
_SESSION = {"sub": "demo-user", "email": "demo-user@example.invalid", "avatar_url": None}
_BANNER = """<style id="investigate-local-demo-banner">
html::before{content:"Local demo · synthetic incident data · no live integrations";
position:fixed;inset:0 0 auto 0;height:32px;z-index:2147483647;display:flex;
align-items:center;justify-content:center;background:#fef3c7;color:#78350f;
font:600 12px/1.2 system-ui,sans-serif;border-bottom:1px solid #f59e0b;pointer-events:none}
body{padding-top:32px!important}
.agents-ui,[data-sidebar-frame]{height:calc(100svh - 32px)!important}
[data-sidebar-expand]{top:40px!important}
</style>"""


class PreviewCrons:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.created

    async def create(self, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        record = {"cron_id": str(uuid4()), "assistant_id": assistant_id, **kwargs}
        self.created.append(record)
        return record


class PreviewThreads:
    async def create(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs


class PreviewRuns:
    async def list(self, thread_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class PreviewRuntime:
    def __init__(self) -> None:
        self.store = FakeStore()
        self.crons = PreviewCrons()
        self.client = SimpleNamespace(
            store=self.store, crons=self.crons, threads=PreviewThreads(), runs=PreviewRuns()
        )
        self.slack_calls: list[str] = []
        self.engine_calls: list[str | None] = []
        self.outbound_attempts: list[str] = []
        self.publications: list[dict[str, Any]] = []
        self.channels: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()
        self.draining = False
        self.wake_requested = False
        self.scheduled: set[asyncio.Task] = set()
        self.original_send = httpx.AsyncClient.send

    async def guarded_send(
        self, client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        local_asset = (
            request.url.scheme == "http"
            and request.url.host in {"127.0.0.1", "localhost"}
            and request.url.port == 3000
            and not request.url.path.startswith("/dashboard/api")
        )
        in_process = isinstance(client._transport, (httpx.ASGITransport, httpx.MockTransport))
        if not local_asset and not in_process:
            self.outbound_attempts.append(str(request.url))
            raise RuntimeError("Local preview blocks every network destination except Vite assets")
        return await self.original_send(client, request, **kwargs)

    async def slack_request(
        self, method: str, *, write: bool = False, **params: Any
    ) -> dict[str, Any]:
        self.slack_calls.append(method)
        if method == "auth.test":
            return {"ok": True, "team_id": WORKSPACE_ID, "user_id": "UDEMOBOT", "bot_id": "BDEMO"}
        if method == "users.info":
            return {"ok": True, "user": {"id": "UDEMO", "profile": {"email": _SESSION["email"]}}}
        channel_id = params.get("channel")
        if channel_id not in self.channels:
            raise slack.SlackError("demo_channel_not_found")
        channel = self.channels[channel_id]
        if method == "conversations.info":
            return {"ok": True, "channel": dict(channel)}
        if method == "conversations.join":
            channel["is_member"] = True
            return {"ok": True, "channel": dict(channel)}
        if method == "conversations.history":
            return {
                "ok": True,
                "messages": self.messages,
                "has_more": False,
                "response_metadata": {"next_cursor": ""},
            }
        if method == "conversations.replies":
            return {"ok": True, "messages": [], "has_more": False}
        if method == "chat.postMessage":
            self.publications.append(params)
            return {"ok": True, "ts": f"{int(time.time())}.{len(self.publications):06d}"}
        raise RuntimeError(f"The local Slack fixture does not implement {method}")

    async def run_engine(
        self, messages, policy, previous_report=None, question=None, *, before_tool_call=None
    ):
        if before_tool_call:
            await before_tool_call()
        self.engine_calls.append(question)
        evidence = [
            Evidence(
                id="demo-slack",
                source="slack",
                url=BASE_URL + "/preview/evidence/slack",
                summary="Responders report checkout failures beginning at 09:44 UTC.",
            ),
            Evidence(
                id="demo-errors",
                source="datadog",
                url=BASE_URL + "/preview/evidence/errors",
                summary="Checkout error rate rose from 0.2% to 18.4%; retries increased 4.1×.",
                query="service:checkout-api status:error · synthetic 09:30–10:00 UTC window",
            ),
            Evidence(
                id="demo-commit",
                source="github",
                url=BASE_URL + "/preview/evidence/commit",
                summary="Release checkout-api-2026.09.07 added immediate retries to payment authorization.",
            ),
        ]
        summary = (
            "Checkout failures increased after the 09:42 release. Immediate payment retries are a plausible amplifier; causality remains unproven."
            if not question
            else "The 09:42 release introduced immediate payment-authorization retries. Errors began two minutes later, and retry volume rose 4.1×. This supports correlation; a deployed SHA and comparison would test the hypothesis."
        )
        return InvestigationReport(
            summary=summary,
            impact="Synthetic checkout failures affect about 18% of requests; browsing and order history remain available.",
            outcome="findings",
            evidence=evidence,
            hypotheses=[
                Hypothesis(
                    title="Payment retries amplify a downstream slowdown",
                    assessment="plausible",
                    evidence_ids=["demo-errors", "demo-commit"],
                ),
                Hypothesis(
                    title="Database saturation explains the failures",
                    assessment="rejected",
                    evidence_ids=["demo-errors"],
                ),
            ],
            checked=[
                "Compared error and retry aggregates before and after the release.",
                "Checked the rollout timeline and the retry-path diff.",
                "Database latency stayed close to its pre-incident baseline.",
            ],
            gaps=[
                "The exact deployed commit has not been verified.",
                "All evidence in this local preview is synthetic.",
            ],
            questions=["Which commit SHA is running on the affected checkout instances?"],
        )

    async def dispatch(
        self, thread_id: str, assistant_id: str, *, input: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        if assistant_id != "investigate":
            raise RuntimeError("Preview dispatch only runs the real Investigate channel worker")
        await worker.process_channel(input["investigation_id"])
        return {"run_id": str(uuid4()), "status": "success"}

    async def wake(self) -> None:
        self.wake_requested = True
        if self.draining:
            return
        async with self.lock:
            self.draining = True
            try:
                for _ in range(50):
                    self.wake_requested = False
                    result = await coordinator.coordinate()
                    if result.get("status") != "dispatched" and not self.wake_requested:
                        return
                raise RuntimeError("Local preview exceeded its coordinator drain budget")
            finally:
                self.draining = False

    async def schedule_wake(self, seconds: float = 0) -> None:
        async def delayed():
            await asyncio.sleep(max(seconds, 0))
            await self.wake()

        task = asyncio.create_task(delayed())
        self.scheduled.add(task)
        task.add_done_callback(self.scheduled.discard)

    async def seed(self) -> None:
        self.store.items.clear()
        self.slack_calls.clear()
        self.engine_calls.clear()
        self.publications.clear()
        self.channels = {
            CHANNEL_ID: {
                "id": CHANNEL_ID,
                "name": "inc-checkout-errors",
                "is_channel": True,
                "is_private": False,
                "is_im": False,
                "is_mpim": False,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
                "is_member": False,
                "is_archived": False,
                "topic": {"value": "Elevated checkout errors following the latest release"},
            }
        }
        now = time.time()
        self.messages = [
            {
                "ts": str(now - 600),
                "user": "UDEMO1",
                "text": "Checkout requests began returning HTTP 503 around 09:44 UTC. About 18% are failing.",
            },
            {
                "ts": str(now - 540),
                "bot_id": "BDEMO",
                "text": "Release checkout-api-2026.09.07 completed at 09:42 UTC; payment authorization retry behavior changed.",
            },
            {
                "ts": str(now - 480),
                "user": "UDEMO2",
                "text": "Retry volume is up 4.1x. Database latency is close to baseline; browsing is unaffected.",
            },
        ]
        await service.POLICIES.put(
            "default",
            InvestigationPolicy(
                enabled=True,
                workspace_id=WORKSPACE_ID,
                slack_app_id=APP_ID,
                channel_prefix="inc-",
                enabled_at=now - 1,
            ),
        )
        await service.accept_slack_event(
            {
                "type": "event_callback",
                "team_id": WORKSPACE_ID,
                "api_app_id": APP_ID,
                "event_id": "DEMO_CHANNEL_CREATED",
                "event_time": now,
                "event": {
                    "type": "channel_created",
                    "channel": {"id": CHANNEL_ID, "name": "inc-checkout-errors"},
                },
            }
        )
        record = await service.INVESTIGATIONS.get(
            service.investigation_id(WORKSPACE_ID, CHANNEL_ID)
        )
        if record:
            await service.INVESTIGATIONS.put(record.id, record)


async def _session() -> dict[str, Any]:
    return dict(_SESSION)


async def _same_origin(request: Request) -> None:
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and request.headers.get("origin") != BASE_URL
    ):
        raise HTTPException(403, "Local demo mutations require the preview origin")


def create_preview_app(*, mount_ui: bool = True) -> FastAPI:
    runtime = PreviewRuntime()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with ExitStack() as patches:
            for module in (agent_store, service, coordinator):
                patches.enter_context(patch.object(module, "store_client", lambda: runtime.client))
            patches.enter_context(patch.object(service, "_wake", runtime.wake))
            patches.enter_context(patch.object(worker, "schedule_wake", runtime.schedule_wake))
            patches.enter_context(patch.object(coordinator, "create_durable_run", runtime.dispatch))
            patches.enter_context(patch.object(slack, "request", runtime.slack_request))
            patches.enter_context(patch.object(worker, "run_engine", runtime.run_engine))
            patches.enter_context(
                patch.object(
                    service,
                    "ENV",
                    SimpleNamespace(
                        SLACK_BOT_TOKEN=SimpleNamespace(get=lambda: "local-demo-placeholder"),
                        SLACK_APP_ID=SimpleNamespace(get=lambda: APP_ID),
                    ),
                )
            )
            original_summary = service.summary

            def summary(record):
                return original_summary(record) | {
                    "slack_url": BASE_URL + "/preview/evidence/slack"
                }

            patches.enter_context(patch.object(service, "summary", summary))

            async def send(client, request, **kwargs):
                return await runtime.guarded_send(client, request, **kwargs)

            patches.enter_context(patch.object(httpx.AsyncClient, "send", send))
            await runtime.seed()
            try:
                yield
            finally:
                for task in runtime.scheduled:
                    task.cancel()
                await asyncio.gather(*runtime.scheduled, return_exceptions=True)
                for route in app.routes:
                    if isinstance(route, dashboard_ui.DashboardDevProxyRoute):
                        await route.client.aclose()

    app = FastAPI(title="Investigate local demo", lifespan=lifespan)
    app.state.preview = runtime
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
    app.dependency_overrides[require_session] = _session
    app.dependency_overrides[investigate_api._responder] = _session
    app.dependency_overrides[investigate_api._administrator] = _session
    app.include_router(
        investigate_api.router, prefix="/dashboard/api", dependencies=[Depends(_same_origin)]
    )

    @app.get("/dashboard/api/me")
    async def me():
        return {
            "login": "demo-user",
            "email": _SESSION["email"],
            "avatar_url": None,
            "is_admin": True,
            "slack_oauth_enabled": False,
        }

    @app.get("/preview/status")
    async def status():
        return {
            "banner": BANNER_TEXT,
            "engine_passes": len(runtime.engine_calls),
            "outbound_attempts": len(runtime.outbound_attempts),
        }

    @app.post("/preview/reset", dependencies=[Depends(_same_origin)])
    async def reset():
        await runtime.seed()
        return {"status": "reset"}

    @app.post("/preview/events", dependencies=[Depends(_same_origin)])
    async def event(payload: dict[str, Any]):
        return await service.accept_slack_event(payload)

    @app.get("/preview/evidence/{source}")
    async def evidence(source: str):
        descriptions = {
            "slack": "09:44 — Checkout errors reported. 09:46 — Retry volume increased. 09:48 — Database latency remained close to baseline.",
            "errors": "Synthetic aggregates: checkout error rate 0.2% → 18.4%; retry volume 1.0× → 4.1×; database p95 23 ms → 24 ms.",
            "commit": "Synthetic change: payment authorization now retries immediately up to three times. Deployment time: 09:42 UTC. Actual deployed SHA: unverified.",
        }
        if source not in descriptions:
            raise HTTPException(404)
        return HTMLResponse(
            f"<!doctype html><html><head><title>Demo evidence</title></head><body style='font:16px system-ui;max-width:760px;margin:60px auto'><h1>Synthetic {html.escape(source)} evidence</h1><p>{html.escape(descriptions[source])}</p><a href='/investigate'>Back to Investigate</a></body></html>"
        )

    @app.get("/")
    async def index():
        return RedirectResponse("/investigate")

    @app.middleware("http")
    async def demo_banner(request: Request, call_next):
        if "text/html" in request.headers.get("accept", ""):
            request.scope["headers"] = [
                (name, value)
                for name, value in request.scope["headers"]
                if name.lower()
                not in {b"accept-encoding", b"cookie", b"authorization", b"x-tss_shell"}
            ]
            request.scope["headers"].extend(
                [(b"accept-encoding", b"identity"), (b"x-tss_shell", b"true")]
            )
        response = await call_next(request)
        if "text/html" not in response.headers.get("content-type", ""):
            return response
        content = b"".join([chunk async for chunk in response.body_iterator]).decode()
        content = content.replace("</head>", _BANNER + "</head>", 1)
        headers = {
            name: value
            for name, value in response.headers.items()
            if name.lower() not in {"content-length", "content-encoding"}
        }
        headers["Content-Security-Policy"] = (
            "connect-src 'self' ws://127.0.0.1:3000 ws://localhost:3000; img-src 'self' data: blob:; form-action 'self'; frame-src 'self'"
        )
        return Response(
            content,
            status_code=response.status_code,
            headers=headers,
            background=response.background,
        )

    if mount_ui:
        with patch.object(
            dashboard_ui,
            "ENV",
            SimpleNamespace(
                DASHBOARD_DEV_SERVER_URL=SimpleNamespace(optional=lambda: VITE_URL),
            ),
        ):
            dashboard_ui.mount_dashboard_ui(app)
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_preview_app(), host="127.0.0.1", port=2025, access_log=False)
