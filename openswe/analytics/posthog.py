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
            **{
                key: value
                for key, value in properties.items()
                if key
                in {
                    "distinct_id",
                    "$process_person_profile",
                    "page_name",
                    "tool",
                    "is_error",
                    "surface",
                    "source",
                    "event_type",
                    "action",
                    "workspace_id",
                    "repository_id",
                    "repo",
                    "pull_request_id",
                }
            },
            "product": "open-swe",
            "environment": ENV.DD_ENV.get(),
            "$ip": "0.0.0.0",
            "$geoip_disable": True,
        },
    }
    traits = properties.get("$set")
    if isinstance(traits, dict):
        capture_properties = payload["properties"]
        if isinstance(capture_properties, dict):
            capture_properties["$set"] = {
                key: value for key, value in traits.items() if key in {"email", "github_login"}
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
                "$set": {"email": email, "github_login": login},
            },
        )
    except Exception:
        logger.warning("PostHog usage capture failed", exc_info=True)


async def record_webhook(event: LoggedEvent) -> None:
    action = event.payload.get("action") if isinstance(event.payload, dict) else None
    repository = event.payload.get("repository") if isinstance(event.payload, dict) else None
    repo = repository.get("full_name") if isinstance(repository, dict) else None
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
            **({"repo": repo} if event.source == "github" and isinstance(repo, str) else {}),
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
            properties={
                "tool": tool,
                "surface": "mcp",
                "is_error": is_error,
                "repo": cfg.repo_full_name,
            },
        )
    except Exception:
        logger.warning("PostHog MCP capture failed", exc_info=True)
