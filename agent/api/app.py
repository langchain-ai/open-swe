"""FastAPI application composition."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..dashboard import router as dashboard_router
from ..dashboard.plan_api import plan_router
from ..dashboard.workflow_approval_api import workflow_approval_router
from ..logging_redaction import install_webhook_token_redaction
from ..webhooks.github_routes import router as github_webhook_router
from ..webhooks.linear_routes import router as linear_webhook_router
from ..webhooks.slack_routes import router as slack_webhook_router
from .health import router as health_router

install_webhook_token_redaction()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from ..tools.schedule_thread_wakeup import _purge_expired_wakeups_best_effort
    from ..utils.model import close_cached_models, validate_local_dev_llm_config
    from ..utils.sandbox import validate_sandbox_startup_config

    validate_sandbox_startup_config()
    validate_local_dev_llm_config()
    await _purge_expired_wakeups_best_effort()
    try:
        yield
    finally:
        await close_cached_models()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    allowed_origins = [
        origin.strip()
        for origin in os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "").split(",")
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
    app.include_router(dashboard_router)
    app.include_router(plan_router)
    app.include_router(workflow_approval_router)
    app.include_router(linear_webhook_router)
    app.include_router(slack_webhook_router)
    app.include_router(health_router)
    app.include_router(github_webhook_router)
    return app


app = create_app()
