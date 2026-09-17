"""Deferred LangSmith cost enrichment for agent usage records."""

import logging
from collections.abc import Mapping
from typing import Any, Literal, NotRequired, TypedDict

from langgraph_sdk.client import LangGraphClient

from agent import database
from agent.analytics.cost_recovery import enqueue_job
from agent.analytics.usage import record_agent_invocation_completion
from agent.invocation import resolve_invocation_id
from agent.utils.run_usage import summarize_run_usage

logger = logging.getLogger(__name__)

_RETRY_DELAYS_SECONDS = (15, 30, 60, 120, 240)


class AgentCostRefresh(TypedDict):
    task: Literal["agent_cost"]
    thread_id: str
    invocation_id: str
    prepare_run_id: str
    invocation_started_at: NotRequired[str]
    run_id: str
    attempt: int


def _value(state: Mapping[str, Any], key: str) -> str | None:
    value = state.get(key)
    return value if isinstance(value, str) and value else None


def _payload(state: Mapping[str, Any], attempt: int) -> AgentCostRefresh | None:
    thread_id = _value(state, "thread_id")
    try:
        invocation_id = resolve_invocation_id(state)
    except ValueError:
        logger.warning("Invalid cost recovery invocation identity")
        return None
    legacy_run_id = _value(state, "run_id")
    if invocation_id is not None and legacy_run_id is not None and invocation_id != legacy_run_id:
        return None
    invocation_id = invocation_id or legacy_run_id
    if thread_id is None or invocation_id is None:
        return None
    payload: AgentCostRefresh = {
        "task": "agent_cost",
        "thread_id": thread_id,
        "invocation_id": invocation_id,
        "prepare_run_id": invocation_id,
        "run_id": invocation_id,
        "attempt": attempt,
    }
    if started_at := _value(state, "invocation_started_at"):
        payload["invocation_started_at"] = started_at
    return payload


async def schedule_agent_cost_refresh(
    state: Mapping[str, Any], *, attempt: int = 0, client: LangGraphClient | None = None
) -> bool:
    """Persist a compatibility cost job without scheduling callbacks."""
    if attempt < 0 or attempt >= len(_RETRY_DELAYS_SECONDS):
        return False
    payload = _payload(state, attempt)
    if payload is None:
        logger.warning(
            "Could not schedule agent cost refresh without a thread and run",
            extra={"usage_attempt": attempt},
        )
        return False
    async with database.transaction() as conn:
        await enqueue_job(
            conn,
            payload["invocation_id"],
            payload["thread_id"],
            payload.get("invocation_started_at"),
        )
    return True


async def run_agent_cost_refresh(
    state: Mapping[str, Any], *, client: LangGraphClient | None = None
) -> dict[str, Any]:
    """Import scheduler payloads without creating another retry chain."""
    payload = _payload(state, 0)
    if payload is None:
        return {"status": "unavailable", "reason": "invalid payload"}
    async with database.transaction() as conn:
        await enqueue_job(
            conn,
            payload["invocation_id"],
            payload["thread_id"],
            payload.get("invocation_started_at"),
        )
    return {"status": "queued"}


async def finalize_agent_invocation_usage(
    *,
    invocation_id: str,
    thread_id: str,
    invocation_started_at: str | None = None,
    state: dict[str, Any] | None,
    status: str = "success",
    failure_code: str | None = None,
) -> None:
    """Persist terminal invocation usage and schedule deferred cost enrichment."""
    try:
        await record_agent_invocation_completion(
            invocation_id=invocation_id,
            thread_id=thread_id,
            invocation_started_at=invocation_started_at,
            status=status,
            failure_code=failure_code,
            usage=summarize_run_usage(state, invocation_id=invocation_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to record completed agent invocation usage",
            extra={
                "cost_error_code": "terminal_commit_failed",
                "cost_error_type": type(exc).__name__,
            },
        )
