"""FastAPI application composition."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..dashboard import router as dashboard_router
from ..dashboard.oauth import allowed_dashboard_origins
from ..dashboard.plan_api import plan_router
from ..dashboard.workflow_approval_api import workflow_approval_router
from ..utils.event_loop import pin_single_event_loop
from ..utils.startup_config import log_startup_configuration
from ..webhooks.common import ensure_slack_bot_identity
from ..webhooks.github_routes import router as github_webhook_router
from ..webhooks.linear_routes import router as linear_webhook_router
from ..webhooks.slack_routes import router as slack_webhook_router
from .health import router as health_router

logger = logging.getLogger(__name__)

# Before the queue starts: it reads this when it builds its workers, and Open SWE
# cannot survive them landing on different loops.
pin_single_event_loop()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from agent.sandboxes.providers import validate_sandbox_startup_config

    from ..utils.model import close_cached_models, validate_local_dev_llm_config

    pin_single_event_loop()
    validate_sandbox_startup_config()
    validate_local_dev_llm_config()
    log_startup_configuration()
    try:
        await ensure_slack_bot_identity()
    except Exception:  # noqa: BLE001
        logger.debug("Slack bot identity discovery failed at startup", exc_info=True)
    try:
        yield
    finally:
        await close_cached_models()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    extra_origins = os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "").split(",")
    if "*" in (origin.strip() for origin in extra_origins):
        raise RuntimeError(
            "DASHBOARD_ALLOWED_ORIGINS must not include '*' when allow_credentials=True"
        )
    # The dashboard's own origin (DASHBOARD_BASE_URL) is always allowed;
    # DASHBOARD_ALLOWED_ORIGINS only adds further cross-origin frontends.
    allowed_origins = sorted(allowed_dashboard_origins())
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )
    app.include_router(dashboard_router)
    app.include_router(plan_router)
    app.include_router(workflow_approval_router)
    app.include_router(linear_webhook_router)
    app.include_router(slack_webhook_router)
    app.include_router(health_router)
    app.include_router(github_webhook_router)
    return app


app = create_app()
