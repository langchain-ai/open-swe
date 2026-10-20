"""Notion webhook HTTP routes."""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import ValidationError

from openswe.notion import webhook as service
from openswe.notion.models import WebhookEvent
from openswe.notion.settings import notion_settings
from openswe.webhooks import common

logger = logging.getLogger(__name__)

router = APIRouter()

HANDLED_EVENT_TYPES = frozenset({"page.created", "page.properties_updated", "comment.created"})


@router.post("/webhooks/notion")
async def notion_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Handle Notion integration webhooks; the work happens in a background task."""
    body = await request.body()
    secret = notion_settings().webhook_secret

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Rejecting Notion webhook with invalid JSON")
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    if isinstance(payload, dict) and isinstance(payload.get("verification_token"), str):
        if secret:
            # A configured subscription never re-sends its token; refuse to reveal
            # anything to a caller probing a live endpoint.
            logger.warning("Ignoring Notion verification request: secret already configured")
            return {"status": "ignored", "reason": "Already verified"}
        # Notion shows no token in its UI; the admin reads it from here once and
        # sets it as NOTION_WEBHOOK_SECRET. It is in the message, not `extra`, because
        # log viewers such as LangSmith's show only the message.
        logger.warning(
            "Notion webhook verification token received: %s — set it as "
            "NOTION_WEBHOOK_SECRET and paste it into the Notion subscription to verify",
            payload["verification_token"],
        )
        return {"status": "ok", "message": "Verification token received"}

    signature = request.headers.get("X-Notion-Signature", "")
    if not common.verify_notion_signature(body, signature, secret):
        logger.warning("Rejecting Notion webhook with invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        event = WebhookEvent.model_validate(payload)
    except ValidationError:
        logger.warning("Ignoring unparseable Notion webhook event")
        return {"status": "ignored", "reason": "Unrecognized event"}

    if event.type not in HANDLED_EVENT_TYPES:
        return {"status": "ignored", "reason": f"Event type {event.type} is not handled"}
    if event.authored_by_agent(notion_settings().agent_user_ids):
        return {"status": "ignored", "reason": "Event authored by the agent"}

    background_tasks.add_task(service.handle_notion_event, event)
    return {"status": "accepted"}


@router.get("/webhooks/notion")
async def notion_webhook_verify() -> dict[str, str]:
    """Health check for the Notion webhook endpoint."""
    return {"status": "ok", "message": "Notion webhook endpoint is active"}
