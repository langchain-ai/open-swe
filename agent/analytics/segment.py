"""Optional Segment usage delivery without tool content or page identifiers."""

import logging

import httpx
from langgraph.config import get_config

from agent.config import ENV
from agent.run_config import RunConfig

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
        async with httpx.AsyncClient(
            base_url="https://api.segment.io/v1/", auth=(key, ""), timeout=2.0
        ) as client:
            identity = {"userId": login, "traits": {"email": email, "product": "open-swe"}}
            response = await client.post("identify", json=identity)
            response.raise_for_status()
            payload: dict[str, object] = {
                "userId": login,
                "properties": {
                    **properties,
                    "product": "open-swe",
                    "environment": ENV.ANALYTICS_ENVIRONMENT.get(),
                },
                "context": {"ip": "0.0.0.0"},
            }
            payload["name" if event_type == "page" else "event"] = name
            response = await client.post(event_type, json=payload)
            response.raise_for_status()
    except Exception:
        logger.warning("Segment usage delivery failed", exc_info=True)


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
