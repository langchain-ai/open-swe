"""Optional Segment usage delivery without tool content or page identifiers."""

import logging

import httpx
from langgraph.config import get_config

from agent.config import ENV
from agent.run_config import RunConfig
from agent.users import User
from agent.webhooks.event_log import LoggedEvent

logger = logging.getLogger(__name__)


async def record_usage(
    *,
    login: str,
    email: str | None,
    event_type: str,
    name: str,
    properties: dict[str, str | bool],
) -> None:
    key = ENV.SEGMENT_WRITE_KEY.get()
    if not key or not login:
        return
    try:
        user = await User.for_login("github", login)
        if user is None:
            logger.warning("Segment usage identity could not be resolved")
            return
        user_id = str(user.id)
        async with httpx.AsyncClient(
            base_url="https://api.segment.io/v1/", auth=(key, ""), timeout=2.0
        ) as client:
            identity = {
                "userId": user_id,
                "traits": {"email": email, "github_login": login, "product": "open-swe"},
            }
            response = await client.post("identify", json=identity)
            response.raise_for_status()
            payload: dict[str, object] = {
                "userId": user_id,
                "properties": {
                    **properties,
                    "product": "open-swe",
                    "environment": ENV.DD_ENV.get(),
                },
                "context": {"ip": "0.0.0.0"},
            }
            payload["name" if event_type == "page" else "event"] = name
            response = await client.post(event_type, json=payload)
            response.raise_for_status()
    except Exception:
        logger.warning("Segment usage delivery failed", exc_info=True)


async def record_webhook(event: LoggedEvent) -> None:
    key = ENV.SEGMENT_WRITE_KEY.get()
    if not key:
        return
    action = event.payload.get("action") if isinstance(event.payload, dict) else None
    properties: dict[str, object] = {
        "source": event.source,
        "event_type": event.base_event_type,
        "action": action if isinstance(action, str) else "",
        "product": "open-swe",
        "surface": "webhook",
        "environment": ENV.DD_ENV.get(),
        **{
            field: str(value) if value else None
            for field, value in {
                "workspace_id": event.workspace_id,
                "repository_id": event.repository_id,
                "pull_request_id": event.pull_request_id,
            }.items()
        },
    }
    payload: dict[str, object] = {
        "event": "Webhook Received",
        "messageId": f"webhook:{event.source}:{event.delivery_id}:{event.received_at.isoformat()}",
        "timestamp": event.received_at.isoformat(),
        "properties": properties,
        "context": {"ip": "0.0.0.0"},
    }
    if event.user_id:
        payload["userId"] = str(event.user_id)
    else:
        payload["anonymousId"] = f"open-swe:webhook:{event.source}"
    try:
        async with httpx.AsyncClient(
            base_url="https://api.segment.io/v1/", auth=(key, ""), timeout=2.0
        ) as client:
            response = await client.post("track", json=payload)
            response.raise_for_status()
    except Exception:
        logger.warning("Segment webhook delivery failed", exc_info=True)


async def record_mcp_tool(tool: str, is_error: bool) -> None:
    if not ENV.SEGMENT_WRITE_KEY.get():
        return
    try:
        cfg = RunConfig.from_config(get_config())
        await record_usage(
            login=cfg.github_login or "",
            email=cfg.user_email,
            event_type="track",
            name="MCP Tool Called",
            properties={"tool": tool, "surface": "mcp", "is_error": is_error},
        )
    except Exception:
        logger.warning("Segment MCP capture failed", exc_info=True)
