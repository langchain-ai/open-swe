"""Azure DevOps Service Hook HTTP routes."""

import os

from fastapi import APIRouter

from . import azure_devops as service
from . import common

router = APIRouter()

AZURE_DEVOPS_WEBHOOK_SECRET = os.environ.get("AZURE_DEVOPS_WEBHOOK_SECRET", "")
AZURE_DEVOPS_WEBHOOK_SECRET_HEADER = os.environ.get(
    "AZURE_DEVOPS_WEBHOOK_SECRET_HEADER",
    "X-Azure-DevOps-Webhook-Secret",
)


@router.post("/webhooks/azure-devops")
async def azure_devops_webhook(
    request: common.Request, background_tasks: common.BackgroundTasks
) -> dict[str, str]:
    """Handle Azure DevOps Service Hooks (work item commented, PR commented)."""
    from ..utils.azure_devops_webhook import (
        azure_devops_service_hook_should_process,
        verify_azure_devops_webhook_secret,
    )

    if not verify_azure_devops_webhook_secret(
        request,
        AZURE_DEVOPS_WEBHOOK_SECRET,
        header_name=AZURE_DEVOPS_WEBHOOK_SECRET_HEADER,
    ):
        common.logger.warning("Invalid or missing Azure DevOps webhook secret")
        raise common.HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = common.json.loads(await request.body())
    except common.json.JSONDecodeError:
        common.logger.exception("Failed to parse Azure DevOps webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    if not azure_devops_service_hook_should_process(payload):
        return {
            "status": "ignored",
            "reason": "Unsupported event or missing @openswe in payload",
        }

    background_tasks.add_task(service.handle_azure_devops_webhook_payload, payload)
    return {"status": "accepted", "message": "Processing Azure DevOps webhook"}


@router.get("/webhooks/azure-devops")
async def azure_devops_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Azure DevOps Service Hook URL checks."""
    return {"status": "ok", "message": "Azure DevOps webhook endpoint is active"}
