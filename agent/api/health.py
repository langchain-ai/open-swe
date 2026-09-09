"""Health and run-completion routes."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agent.completion import handle_run_completion, verify_run_complete_token

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy"}


@router.get("/health/analytics")
async def analytics_health_check() -> dict[str, Any]:
    from agent.analytics.database import readiness

    return await readiness()


@router.post("/webhooks/run-complete")
async def run_complete_webhook(request: Request) -> dict[str, Any]:
    if not verify_run_complete_token(request.query_params.get("token")):
        raise HTTPException(status_code=401, detail="Invalid run-complete token")
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return {"status": "error", "message": "Invalid JSON"}
    if not isinstance(payload, dict):
        return {"status": "ignored", "reason": "payload not an object"}
    return await handle_run_completion(payload)
