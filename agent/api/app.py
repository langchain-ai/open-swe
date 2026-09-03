"""FastAPI application composition."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.api.health import router as health_router
from agent.dashboard import router as dashboard_router
from agent.dashboard.inline_review_api import inline_review_router
from agent.dashboard.plan_api import plan_router
from agent.dashboard.workflow_approval_api import workflow_approval_router
from agent.github.routes import router as github_webhook_router
from agent.linear.routes import router as linear_webhook_router
from agent.slack.routes import router as slack_webhook_router
from agent.utils.event_loop import pin_single_event_loop

# Before the queue starts: it reads this when it builds its workers, and Open SWE
# cannot survive them landing on different loops.
pin_single_event_loop()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from agent.sandboxes.providers.registry import validate_sandbox_startup_config
    from agent.utils.model import close_cached_models, validate_local_dev_llm_config

    pin_single_event_loop()
    validate_sandbox_startup_config()
    validate_local_dev_llm_config()
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
    app.include_router(inline_review_router)
    app.include_router(workflow_approval_router)
    app.include_router(linear_webhook_router)
    app.include_router(slack_webhook_router)
    app.include_router(health_router)
    app.include_router(github_webhook_router)
    return app


app = create_app()
