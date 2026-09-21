"""FastAPI application composition."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.api.health import router as health_router
from agent.api.tracing import add_trace_resource_names
from agent.api_keys.public_routes import router as api_key_public_router
from agent.config import ENV
from agent.dashboard import router as dashboard_router
from agent.github.routes import router as github_webhook_router
from agent.linear.routes import router as linear_webhook_router
from agent.sandboxes.tool_routes import router as sandbox_tool_router
from agent.slack.routes import router as slack_webhook_router
from agent.threads.plan_api import plan_router
from agent.threads.workflow_approval_api import workflow_approval_router
from agent.utils.dashboard_ui import mount_dashboard_ui
from agent.utils.event_loop import pin_single_event_loop

logger = logging.getLogger(__name__)

# Before the queue starts: it reads this when it builds its workers, and Open SWE
# cannot survive them landing on different loops.
pin_single_event_loop()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from agent import database
    from agent.analytics.worker import start_worker, stop_worker
    from agent.dashboard.admin import configured_admins
    from agent.dashboard.oauth import validate_github_login_allowlist
    from agent.database.analytics import activate_reporting, load_workspace
    from agent.sandboxes.providers.registry import validate_sandbox_startup_config
    from agent.transcript import listener as transcript_listener
    from agent.users import User
    from agent.users.import_store import import_user_mappings
    from agent.utils.model import close_cached_models, validate_local_dev_llm_config
    from agent.workspaces.store import import_store_records

    pin_single_event_loop()
    validate_github_login_allowlist()
    validate_sandbox_startup_config()
    validate_local_dev_llm_config()
    database.require_configured()
    await database.migrate()
    try:
        # Workspaces used to live in the LangGraph Store; this empties it into
        # the tables and is a no-op once it has.
        imported = await import_store_records()
    except Exception:  # noqa: BLE001
        # Startup continues, but repository routing fails closed until an import
        # succeeds: the tables this failed to fill make every repository read as
        # unowned, and GitHub retries a 503 while it drops a 200.
        logger.exception("Importing workspaces from the LangGraph Store failed")
    else:
        logger.info(
            "Imported workspaces from the LangGraph Store",
            extra={"imported_workspaces": imported},
        )
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
        yield
    finally:
        await transcript_listener.stop()
        await stop_worker()
        await database.close()
        await close_cached_models()


def create_app() -> FastAPI:
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
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )
    add_trace_resource_names(app)
    app.include_router(dashboard_router)
    app.include_router(plan_router)
    app.include_router(workflow_approval_router)
    app.include_router(linear_webhook_router)
    app.include_router(slack_webhook_router)
    app.include_router(health_router)
    app.include_router(github_webhook_router)
    app.include_router(sandbox_tool_router)
    app.include_router(api_key_public_router)
    mount_dashboard_ui(app)
    return app


app = create_app()
