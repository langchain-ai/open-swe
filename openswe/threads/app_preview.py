"""Workspace links to a thread sandbox's ports, for the dashboard's app preview."""

import logging
from typing import Annotated, Any

import httpx2
from fastapi import APIRouter, HTTPException, Path, Response

from openswe.audit_logs.middleware import audit_endpoint
from openswe.config import ENV
from openswe.dashboard.deps import SESSION_DEP
from openswe.sandboxes.providers.langsmith import create_workspace_service_url
from openswe.threads.handlers import get_dashboard_live_sandbox
from openswe.utils.dashboard_links import dashboard_embed_origins

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])


@router.post("/threads/{thread_id}/ports/{port}/service-url")
@audit_endpoint
async def api_thread_port_service_url(
    thread_id: str,
    port: Annotated[int, Path(ge=1, le=65535)],
    response: Response,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, str]:
    if ENV.SANDBOX_TYPE.get() != "langsmith":
        raise HTTPException(409, "App previews require a LangSmith sandbox")
    sandbox_id, _ = await get_dashboard_live_sandbox(
        thread_id, session["sub"], email=session.get("email")
    )
    try:
        url = await create_workspace_service_url(
            sandbox_id, port, embed_origins=dashboard_embed_origins()
        )
    except httpx2.HTTPStatusError as exc:
        logger.warning(
            "Sharing a sandbox port failed",
            extra={
                "thread_id": thread_id,
                "sandbox_id": sandbox_id,
                "port": port,
                "status_code": exc.response.status_code,
            },
        )
        raise HTTPException(502, f"LangSmith could not share port {port}") from exc
    response.headers["Cache-Control"] = "no-store"
    return {"url": url}
