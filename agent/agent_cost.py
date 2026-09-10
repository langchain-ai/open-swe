"""Deferred LangSmith cost enrichment for agent usage records."""

import logging
from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from langgraph_sdk.client import LangGraphClient

from agent.dashboard.agent_usage import (
    agent_invocation_needs_cost_refresh,
    mark_agent_invocation_cost_refresh_scheduled,
    record_agent_invocation_completion,
    record_agent_invocation_cost,
)
from agent.invocation import resolve_invocation_id
from agent.utils.langsmith import LangSmithCostUnavailable, get_langsmith_thread_cost
from agent.utils.run_usage import summarize_run_usage
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_RETRY_DELAYS_SECONDS = (15, 30, 60, 120, 240)


class AgentCostRefresh(TypedDict):
    task: Literal["agent_cost"]
    thread_id: str
    invocation_id: str
    prepare_run_id: str
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
        return None
    legacy_run_id = _value(state, "run_id")
    if invocation_id is not None and legacy_run_id is not None and invocation_id != legacy_run_id:
        return None
    invocation_id = invocation_id or legacy_run_id
    if thread_id is None or invocation_id is None:
        return None
    return {
        "task": "agent_cost",
        "thread_id": thread_id,
        "invocation_id": invocation_id,
        "prepare_run_id": invocation_id,
        "run_id": invocation_id,
        "attempt": attempt,
    }


async def schedule_agent_cost_refresh(
    state: Mapping[str, Any], *, attempt: int = 0, client: LangGraphClient | None = None
) -> bool:
    """Schedule one stateless, delayed cost refresh attempt."""
    if attempt < 0 or attempt >= len(_RETRY_DELAYS_SECONDS):
        return False
    payload = _payload(state, attempt)
    if payload is None:
        logger.warning(
            "Could not schedule agent cost refresh without a thread and run",
            extra={"usage_attempt": attempt},
        )
        return False
    client = client or langgraph_client()
    try:
        await client.runs.create(
            None,
            "scheduler",
            input=payload,
            metadata={"kind": "agent_cost_refresh", "run_id": payload["invocation_id"]},
            after_seconds=_RETRY_DELAYS_SECONDS[attempt],
            on_completion="delete",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not schedule agent cost refresh",
            extra={"usage_run_id": payload["invocation_id"], "usage_attempt": attempt},
            exc_info=True,
        )
        return False
    return True


async def run_agent_cost_refresh(
    state: Mapping[str, Any], *, client: LangGraphClient | None = None
) -> dict[str, Any]:
    """Store one run's cost or enqueue the next bounded attempt."""
    client = client or langgraph_client()
    raw_attempt = state.get("attempt")
    attempt = (
        raw_attempt if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) else -1
    )
    payload = _payload(state, attempt)
    if payload is None or attempt < 0 or attempt >= len(_RETRY_DELAYS_SECONDS):
        return {"status": "unavailable", "reason": "invalid payload"}

    try:
        snapshot = await get_langsmith_thread_cost(
            payload["thread_id"], payload["invocation_id"], run_only=True
        )
    except LangSmithCostUnavailable as exc:
        logger.info(
            "Agent cost refresh unavailable",
            extra={
                "usage_run_id": payload["invocation_id"],
                "usage_attempt": attempt,
                "usage_reason": str(exc),
            },
        )
        return {"status": "unavailable", "reason": str(exc)}
    except Exception:  # noqa: BLE001
        logger.warning(
            "Agent cost refresh attempt failed",
            extra={"usage_run_id": payload["invocation_id"], "usage_attempt": attempt},
            exc_info=True,
        )
        snapshot = None
    if snapshot is not None:
        try:
            await record_agent_invocation_cost(
                invocation_id=payload["invocation_id"], cost_usd=snapshot.total_cost
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not persist agent run cost",
                extra={"usage_run_id": payload["invocation_id"], "usage_attempt": attempt},
                exc_info=True,
            )
            snapshot = None
        else:
            return {"status": "updated"}

    next_attempt = attempt + 1
    if next_attempt >= len(_RETRY_DELAYS_SECONDS):
        logger.info(
            "Agent cost refresh exhausted",
            extra={"usage_run_id": payload["invocation_id"], "usage_attempt": attempt},
        )
        return {"status": "exhausted", "reason": "LangSmith cost unavailable"}
    scheduled = await schedule_agent_cost_refresh(state, attempt=next_attempt, client=client)
    return {
        "status": "retry_scheduled" if scheduled else "unavailable",
        "reason": "LangSmith cost unavailable",
        "attempt": next_attempt,
    }


async def finalize_agent_invocation_usage(
    *, invocation_id: str, thread_id: str, state: dict[str, Any] | None
) -> None:
    """Persist terminal invocation usage and schedule deferred cost enrichment."""
    try:
        recorded = await record_agent_invocation_completion(
            invocation_id=invocation_id,
            usage=summarize_run_usage(state, invocation_id=invocation_id),
        )
        if not recorded and not await agent_invocation_needs_cost_refresh(
            invocation_id=invocation_id
        ):
            return
        scheduled = await schedule_agent_cost_refresh(
            {"thread_id": thread_id, "invocation_id": invocation_id}
        )
        if scheduled:
            await mark_agent_invocation_cost_refresh_scheduled(invocation_id=invocation_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to record completed agent invocation usage",
            extra={"usage_invocation_id": invocation_id, "usage_thread_id": thread_id},
            exc_info=True,
        )


async def finalize_agent_run_usage(
    *, run_id: str, thread_id: str, state: dict[str, Any] | None
) -> None:
    """Compatibility wrapper for legacy callers."""
    await finalize_agent_invocation_usage(
        invocation_id=run_id,
        thread_id=thread_id,
        state=state,
    )
