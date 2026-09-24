import logging
import secrets
import time
import uuid
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config

from agent.run_config import RunConfig
from agent.runtime.execution import bindable_config
from agent.utils.langsmith import create_langsmith_thread_feedback
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


async def _collect_thread_details() -> dict[str, Any]:
    """Best-effort diagnostics; every failure here is swallowed so a report still lands."""
    details: dict[str, Any] = {}
    try:
        cfg = RunConfig.from_config(bindable_config(get_config()))
    except Exception:
        logger.debug("Could not read platform issue run config", exc_info=True)
        return details
    details["configurable"] = cfg.dump()
    if cfg.thread_id:
        try:
            details["thread"] = await langgraph_client().threads.get(cfg.thread_id)
        except Exception:
            logger.debug("Could not load platform issue thread details", exc_info=True)
    return details


async def report_platform_issue(
    problem_description: str,
    keywords: list[str],
) -> dict[str, str]:
    """Implement the `report_platform_issue` tool."""
    description = problem_description.strip()
    if not description:
        raise ValueError("Describe the platform issue in problem_description.")
    report_id = _uuid7()
    thread_details = await _collect_thread_details()
    logger.warning(
        "Platform issue reported",
        extra={
            "platform_issue_report_id": report_id,
            "platform_issue_description": description,
            "platform_issue_keywords": keywords,
            "platform_issue_thread_details": _redact_sensitive(thread_details),
        },
    )
    exported = False
    thread_id = thread_details.get("configurable", {}).get("thread_id") or ""
    if thread_id:
        try:
            exported = await create_langsmith_thread_feedback(
                thread_id,
                "platform_issue",
                score=0.0,
                comment=description,
                source_info={
                    "source": "report_platform_issue_tool",
                    "report_id": report_id,
                    "keywords": keywords,
                },
            )
        except Exception:
            logger.exception("Could not export platform issue report %s", report_id)
    return {"report_id": report_id, "export_status": "exported" if exported else "logged_only"}
