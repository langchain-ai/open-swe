import logging
import secrets
import time
import uuid
from collections.abc import Mapping
from typing import Any

from agent.run_config import RunConfig
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)
_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token")


def _redact_sensitive(value: Any, key: str = "") -> Any:
    normalized_key = key.lower().replace("-", "_")
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_sensitive(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


def _uuid7() -> str:
    timestamp_ms = (time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    uuid_int = (timestamp_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=uuid_int))


async def report_platform_issue(
    problem_description: str,
    keywords: list[str],
) -> dict[str, str]:
    """Implement the `report_platform_issue` tool."""
    report_id = _uuid7()
    cfg = RunConfig.from_runtime()
    thread_details: dict[str, Any] = {"configurable": cfg.dump()}
    if cfg.thread_id:
        try:
            thread_details["thread"] = await langgraph_client().threads.get(cfg.thread_id)
        except Exception:
            logger.debug("Could not load platform issue thread details", exc_info=True)
    logger.warning(
        "Platform issue reported",
        extra={
            "platform_issue_report_id": report_id,
            "platform_issue_description": problem_description,
            "platform_issue_keywords": keywords,
            "platform_issue_thread_details": _redact_sensitive(thread_details),
        },
    )
    return {"report_id": report_id}
