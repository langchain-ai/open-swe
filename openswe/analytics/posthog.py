"""Optional PostHog capture without tool content or page identifiers."""

import logging

import httpx
from langgraph.config import get_config

from openswe.config import ENV
from openswe.run_config import RunConfig
from openswe.users import User
from openswe.webhooks.event_log import LoggedEvent

logger = logging.getLogger(__name__)


async def _capture(
    event: str, properties: dict[str, object], *, timestamp: str | None = None
) -> None:
    key = ENV.POSTHOG_API_KEY.get()
    if not key:
        return
    payload: dict[str, object] = {
        "api_key": key,
        "event": event,
        "properties": {
            **properties,
            "product": "open-swe",
            "environment": ENV.DD_ENV.get(),
            "$ip": "0.0.0.0",
            "$geoip_disable": True,
        },
    }
    if timestamp:
        payload["timestamp"] = timestamp
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(
                f"{ENV.POSTHOG_HOST.get().rstrip('/')}/i/v0/e/", json=payload
            )
            response.raise_for_status()
    except Exception:
        logger.warning("PostHog usage delivery failed", exc_info=True)


async def record_usage(
    *,
    login: str,
    email: str | None,
    event_type: str,
    name: str,
    properties: dict[str, str | bool],
) -> None:
    if not ENV.POSTHOG_API_KEY.get() or not login:
        return
    try:
        user = await User.for_login("github", login)
        if user is None:
            logger.warning("PostHog usage identity could not be resolved")
            return
        await _capture(
            "$pageview" if event_type == "page" else name,
            {
                **properties,
                "distinct_id": str(user.id),
                "$set": {"email": email, "github_login": login, "product": "open-swe"},
            },
        )
    except Exception:
        logger.warning("PostHog usage capture failed", exc_info=True)


async def record_webhook(event: LoggedEvent) -> None:
    action = event.payload.get("action") if isinstance(event.payload, dict) else None
    await _capture(
        "Webhook Received",
        {
            "distinct_id": str(event.user_id)
            if event.user_id
            else f"open-swe:webhook:{event.source}",
            "$process_person_profile": bool(event.user_id),
            "source": event.source,
            "event_type": event.base_event_type,
            "action": action if isinstance(action, str) else "",
            "surface": "webhook",
            **{
                field: str(value) if value else None
                for field, value in {
                    "workspace_id": event.workspace_id,
                    "repository_id": event.repository_id,
                    "pull_request_id": event.pull_request_id,
                }.items()
            },
        },
        timestamp=event.received_at.isoformat(),
    )


async def record_mcp_tool(tool: str, is_error: bool) -> None:
    if not ENV.POSTHOG_API_KEY.get():
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
        logger.warning("PostHog MCP capture failed", exc_info=True)
