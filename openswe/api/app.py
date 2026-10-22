"""FastAPI application composition."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.routing import Route

from openswe.api.github_errors import add_github_error_handlers
from openswe.api.health import router as health_router
from openswe.api.request_ids import add_request_ids
from openswe.api.tracing import add_trace_resource_names, configure_datadog_environment
from openswe.audit_logs.middleware import AuditLogMiddleware
from openswe.config import ENV
from openswe.dashboard import router as dashboard_router
from openswe.github.routes import router as github_webhook_router
from openswe.linear.routes import router as linear_webhook_router
from openswe.mcp import server as agent_mcp_server
from openswe.notion.routes import router as notion_webhook_router
from openswe.openai_responses.routes import router as sandbox_openai_router
from openswe.remote_runtime import server as remote_runtime_server
from openswe.review.eval_report_routes import router as reviewer_eval_report_router
from openswe.rollout_events import router as rollout_webhook_router
from openswe.sandboxes.tool_routes import router as sandbox_tool_router
from openswe.slack.routes import router as slack_webhook_router
from openswe.threads.plan_api import plan_router
from openswe.threads.router import ThreadRouter
from openswe.threads.workflow_approval_api import workflow_approval_router
from openswe.users.records import UnknownUser
from openswe.utils.dashboard_ui import mount_dashboard_ui
from openswe.utils.event_loop import pin_single_event_loop
from openswe.utils.http_pool import close_pool as close_http_pool

logger = logging.getLogger(__name__)

# Before the queue starts: it reads this when it builds its workers, and Open SWE
# cannot survive them landing on different loops.
pin_single_event_loop()

AGENT_MCP = agent_mcp_server.build_mount()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from openswe import database
    from openswe.analytics.worker import start_worker, stop_worker
    from openswe.bridge import listener as bridge_listener
    from openswe.dashboard.admin import configured_admins
    from openswe.dashboard.oauth import validate_github_login_allowlist
    from openswe.database.analytics import activate_reporting, load_workspace
    from openswe.database.notifications import LISTENER
    from openswe.jobs.kinds import WORKER as JOB_WORKER
    from openswe.jobs.kinds import start_jobs
    from openswe.remote_runtime.client import validate_mda_startup_config
    from openswe.review.styles import delete_analyzer_crons
    from openswe.sandboxes.providers.registry import validate_sandbox_startup_config
    from openswe.transcript import listener as transcript_listener
    from openswe.ui_invalidations.hub import HUB
    from openswe.users import User
    from openswe.utils.model import validate_local_dev_llm_config

    pin_single_event_loop()
    validate_github_login_allowlist()
    validate_sandbox_startup_config()
    validate_local_dev_llm_config()
    validate_mda_startup_config()
    database.require_configured()
    await database.migrate()
    try:
        # The review-style analyzer is gone; its nightly crons would fail every run.
        await delete_analyzer_crons()
    except Exception:  # noqa: BLE001
        logger.exception("Deleting review-style analyzer crons failed")
    if admins := configured_admins():
        await User.sync_admins(admins)
    try:
        await load_workspace()
        await activate_reporting()
        await start_worker()
    except Exception:  # noqa: BLE001
        logger.warning("Analytics startup failed", exc_info=True)
    try:
        await transcript_listener.start()
    except Exception:  # noqa: BLE001
        # Transcript readers fall back to in-process notifications; a thread
        # driven from another process is what goes quiet until this recovers.
        logger.warning("Transcript listener startup failed", exc_info=True)
    try:
        await bridge_listener.start()
    except Exception:  # noqa: BLE001
        # Bridge waiters fall back to in-process notifications and their own
        # liveness ticks; what goes quiet is a bridge driven from another replica.
        logger.warning("Sandbox bridge listener startup failed", exc_info=True)
    try:
        await HUB.start()
    except Exception:  # noqa: BLE001
        # Dashboard pages still load and refetch on their own actions; what
        # stops is hearing about changes made elsewhere.
        logger.warning("UI invalidation hub startup failed", exc_info=True)
    LISTENER.start()
    JOB_WORKER.start(first=start_jobs)
    try:
        from openswe.server import warm_agent_build

        warm_agent_build()
    except Exception:  # noqa: BLE001
        # Runs still build whatever is missing on first use, just more slowly.
        logger.warning("Warming the agent build failed", exc_info=True)
    try:
        async with AsyncExitStack() as stack:
            if AGENT_MCP is not None:
                await stack.enter_async_context(AGENT_MCP.app.lifespan(AGENT_MCP.app))
            yield
    finally:
        await JOB_WORKER.stop()
        await HUB.stop()
        await bridge_listener.stop()
        await transcript_listener.stop()
        await LISTENER.stop()
        await stop_worker()
        await close_http_pool()
        await database.close()


async def _unknown_user(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=409)


def create_app() -> FastAPI:
    configure_datadog_environment()
    app = FastAPI(lifespan=lifespan)
    allowed_origins = [
        origin.strip()
        for origin in ENV.DASHBOARD_ALLOWED_ORIGINS.get().split(",")
        if origin.strip()
    ]
    if "*" in allowed_origins:
        raise RuntimeError(
            "DASHBOARD_ALLOWED_ORIGINS must not include '*' when allow_credentials=True"
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[*allowed_origins, "open-swe://app"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(AuditLogMiddleware)
    # Ahead of the core /threads and /runs routes, which the custom app shadows.
    app.add_middleware(ThreadRouter)
    # Starlette leaves `text/event-stream` and already-encoded responses alone.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=4)
    add_trace_resource_names(app)
    add_request_ids(app)
    add_github_error_handlers(app)
    app.add_exception_handler(UnknownUser, _unknown_user)
    app.include_router(dashboard_router)
    app.include_router(plan_router)
    app.include_router(workflow_approval_router)
    app.include_router(linear_webhook_router)
    app.include_router(notion_webhook_router)
    app.include_router(slack_webhook_router)
    app.include_router(health_router)
    app.include_router(github_webhook_router)
    app.include_router(rollout_webhook_router)
    app.include_router(sandbox_tool_router)
    app.include_router(sandbox_openai_router)
    app.include_router(reviewer_eval_report_router)
    # Routed at the exact path so no redirect sits between a remote runtime and its hooks.
    app.router.routes.append(
        Route(remote_runtime_server.HOOKS_PATH, remote_runtime_server.HOOKS, methods=["POST"])
    )
    if AGENT_MCP is not None:
        app.state.fastmcp_server = AGENT_MCP.app.state.fastmcp_server
        app.router.routes.extend(AGENT_MCP.root_routes)
        app.mount(agent_mcp_server.PREFIX, AGENT_MCP.app)
    mount_dashboard_ui(app)
    return app


app = create_app()
