"""Deferred LangSmith cost enrichment for agent usage records."""

import logging
from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from langgraph_sdk.client import LangGraphClient

from agent.dashboard.agent_usage import record_agent_run_cost
from agent.utils.langsmith import LangSmithCostUnavailable, get_langsmith_thread_cost
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_RETRY_DELAYS_SECONDS = (15, 30, 60, 120, 240)


class AgentCostRefresh(TypedDict):
    task: Literal["agent_cost"]
    thread_id: str
    run_id: str
    attempt: int


def _value(state: Mapping[str, Any], key: str) -> str | None:
    value = state.get(key)
    return value if isinstance(value, str) and value else None


def _payload(state: Mapping[str, Any], attempt: int) -> AgentCostRefresh | None:
    thread_id = _value(state, "thread_id")
    run_id = _value(state, "run_id")
    if thread_id is None or run_id is None:
        return None
    return {
        "task": "agent_cost",
        "thread_id": thread_id,
        "run_id": run_id,
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
        return False
    client = client or langgraph_client()
    try:
        await client.runs.create(
            None,
            "scheduler",
            input=payload,
            metadata={"kind": "agent_cost_refresh", "run_id": payload["run_id"]},
            after_seconds=_RETRY_DELAYS_SECONDS[attempt],
            on_completion="delete",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not schedule agent cost refresh",
            extra={"usage_run_id": payload["run_id"], "usage_attempt": attempt},
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
            payload["thread_id"], payload["run_id"], run_only=True
        )
    except LangSmithCostUnavailable as exc:
        if exc.permanent:
            logger.warning(
                "Agent cost refresh unavailable",
                extra={"usage_run_id": payload["run_id"], "usage_attempt": attempt},
            )
            return {"status": "unavailable", "reason": str(exc)}
        snapshot = None
    except Exception:  # noqa: BLE001
        logger.warning(
            "Agent cost refresh attempt failed",
            extra={"usage_run_id": payload["run_id"], "usage_attempt": attempt},
            exc_info=True,
        )
        snapshot = None
    if snapshot is not None:
        try:
            await record_agent_run_cost(run_id=payload["run_id"], cost_usd=snapshot.total_cost)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not persist agent run cost",
                extra={"usage_run_id": payload["run_id"], "usage_attempt": attempt},
                exc_info=True,
            )
            snapshot = None
        else:
            return {"status": "updated"}

    next_attempt = attempt + 1
    if next_attempt >= len(_RETRY_DELAYS_SECONDS):
        logger.warning(
            "Agent cost refresh exhausted",
            extra={"usage_run_id": payload["run_id"], "usage_attempt": attempt},
        )
        return {"status": "exhausted", "reason": "LangSmith cost unavailable"}
    scheduled = await schedule_agent_cost_refresh(state, attempt=next_attempt, client=client)
    if not scheduled:
        logger.warning(
            "Agent cost refresh unavailable",
            extra={"usage_run_id": payload["run_id"], "usage_attempt": attempt},
        )
    return {
        "status": "retry_scheduled" if scheduled else "unavailable",
        "reason": "LangSmith cost unavailable",
        "attempt": next_attempt,
    }
