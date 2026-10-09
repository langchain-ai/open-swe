"""FastAPI application composition."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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
from openswe.openai_responses.routes import router as sandbox_openai_router
from openswe.remote_runtime import server as remote_runtime_server
from openswe.rollout_events import router as rollout_webhook_router
from openswe.sandboxes.tool_routes import router as sandbox_tool_router
from openswe.slack.routes import router as slack_webhook_router
from openswe.threads.plan_api import plan_router
from openswe.threads.workflow_approval_api import workflow_approval_router
from openswe.users.records import UnknownUser
from openswe.utils.dashboard_ui import mount_dashboard_ui
from openswe.utils.event_loop import pin_single_event_loop

logger = logging.getLogger(__name__)

# Before the queue starts: it reads this when it builds its workers, and Open SWE
# cannot survive them landing on different loops.
pin_single_event_loop()

REMOTE_RUNTIME = remote_runtime_server.build_mount()
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
    from openswe.database.store_imports import run_store_import
    from openswe.sandboxes.providers.registry import validate_sandbox_startup_config
    from openswe.schedules.store import import_store_automations
    from openswe.skill_store.store import import_store_skills
    from openswe.threads.blobs import run_blob_import
    from openswe.transcript import listener as transcript_listener
    from openswe.ui_invalidations.hub import HUB
    from openswe.users import User
    from openswe.users.import_concierge_mode import import_concierge_mode
    from openswe.users.import_records import import_user_records
    from openswe.users.import_store import import_user_mappings
    from openswe.utils.model import validate_local_dev_llm_config

    pin_single_event_loop()
    validate_github_login_allowlist()
    validate_sandbox_startup_config()
    validate_local_dev_llm_config()
    database.require_configured()
    await database.migrate()
    try:
        # People used to be Store records keyed by GitHub login; this moves them
        # into the users table and is a no-op once it has.
        imported_users = await import_user_mappings()
    except Exception:  # noqa: BLE001
        # Startup continues: anyone still in the Store cannot vote or be
        # resolved from Slack until an import succeeds, and nothing else breaks.
        logger.exception("Importing user mappings from the LangGraph Store failed")
    else:
        logger.info(
            "Imported user mappings from the LangGraph Store",
            extra={"imported_users": imported_users},
        )
    try:
        # Concierge mode used to be a Store profile flag; this moves it into
        # users.preferences and is a no-op once it has.
        await import_concierge_mode()
    except Exception:  # noqa: BLE001
        # Startup continues: opted-in people get a thread per DM message until
        # an import succeeds.
        logger.exception("Importing concierge mode from the LangGraph Store failed")
    try:
        # Profiles, preferences, instructions, and tokens used to be Store
        # records keyed by GitHub login; this moves them into user_record.
        await run_store_import("user_records", import_user_records)
    except Exception:  # noqa: BLE001
        # Startup continues: people whose records are still in the Store see
        # default settings and reconnect until an import succeeds.
        logger.exception("Importing per-person records from the LangGraph Store failed")
    try:
        # Automations used to live in the LangGraph Store; this copies them into
        # PostgreSQL and is a no-op once the Store namespaces are empty.
        imported_automations = await import_store_automations()
    except Exception:  # noqa: BLE001
        # Startup continues: automations still in the Store do not run until an
        # import succeeds.
        logger.exception("Importing automations from the LangGraph Store failed")
    else:
        logger.info(
            "Imported automations from the LangGraph Store",
            extra={"imported_automations": imported_automations},
        )
    try:
        # Skills used to live in the LangGraph Store; this moves them into PostgreSQL.
        await run_store_import("skills", import_store_skills)
    except Exception:  # noqa: BLE001
        # Startup continues: skills still in the Store are missing until an
        # import succeeds.
        logger.exception("Importing skills from the LangGraph Store failed")
    blob_import = asyncio.create_task(run_blob_import())
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
    try:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(REMOTE_RUNTIME.lifespan())
            if AGENT_MCP is not None:
                await stack.enter_async_context(AGENT_MCP.app.lifespan(AGENT_MCP.app))
            yield
    finally:
        blob_import.cancel()
        await HUB.stop()
        await bridge_listener.stop()
        await transcript_listener.stop()
        await LISTENER.stop()
        await stop_worker()
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
    add_trace_resource_names(app)
    add_request_ids(app)
    add_github_error_handlers(app)
    app.add_exception_handler(UnknownUser, _unknown_user)
    app.include_router(dashboard_router)
    app.include_router(plan_router)
    app.include_router(workflow_approval_router)
    app.include_router(linear_webhook_router)
    app.include_router(slack_webhook_router)
    app.include_router(health_router)
    app.include_router(github_webhook_router)
    app.include_router(rollout_webhook_router)
    app.include_router(sandbox_tool_router)
    app.include_router(sandbox_openai_router)
    # Routed at the exact path so no redirect sits between a remote runtime and its tools.
    app.router.routes.append(
        Route(remote_runtime_server.PATH, REMOTE_RUNTIME.app, methods=["GET", "POST", "DELETE"])
    )
    app.router.routes.append(
        Route(remote_runtime_server.HOOKS_PATH, REMOTE_RUNTIME.hooks, methods=["POST"])
    )
    if AGENT_MCP is not None:
        app.router.routes.extend(AGENT_MCP.root_routes)
        app.mount(agent_mcp_server.PREFIX, AGENT_MCP.app)
    mount_dashboard_ui(app)
    return app


app = create_app()
