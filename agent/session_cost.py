"""Deferred cumulative LangSmith cost enrichment for Slack replies."""

import logging
from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from langgraph_sdk.client import LangGraphClient

from agent.platforms.slack.client import (
    fetch_slack_thread_message_by_ts,
    format_slack_session_cost,
    lookup_slack_run_message_mapping,
    update_slack_message,
    with_slack_session_cost,
)

from .utils.langsmith import LangSmithCostUnavailable, get_langsmith_thread_cost
from .utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_RETRY_DELAYS_SECONDS = (15, 30, 60, 120, 240)


class SessionCostRefresh(TypedDict):
    task: Literal["session_cost"]
    agent_thread_id: str
    run_id: str
    prepare_run_id: str
    channel_id: str
    thread_ts: str
    attempt: int


def _value(state: Mapping[str, Any], key: str) -> str | None:
    value = state.get(key)
    return value if isinstance(value, str) and value else None


def _payload(state: Mapping[str, Any], attempt: int) -> SessionCostRefresh | None:
    values = {
        key: _value(state, key)
        for key in ("agent_thread_id", "run_id", "prepare_run_id", "channel_id", "thread_ts")
    }
    if any(value is None for value in values.values()):
        return None
    return {
        "task": "session_cost",
        "agent_thread_id": values["agent_thread_id"] or "",
        "run_id": values["run_id"] or "",
        "prepare_run_id": values["prepare_run_id"] or "",
        "channel_id": values["channel_id"] or "",
        "thread_ts": values["thread_ts"] or "",
        "attempt": attempt,
    }


async def schedule_session_cost_refresh(
    state: Mapping[str, Any],
    *,
    attempt: int = 0,
    client: LangGraphClient | None = None,
) -> bool:
    """Schedule one stateless, delayed refresh attempt."""
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
            metadata={"kind": "session_cost_refresh", "run_id": payload["run_id"]},
            after_seconds=_RETRY_DELAYS_SECONDS[attempt],
            on_completion="delete",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Could not schedule session-cost refresh", exc_info=True)
        return False
    return True


def _blocks_contain(blocks: list[dict[str, Any]] | None, text: str) -> bool:
    if blocks is None:
        return True
    for block in blocks:
        values = [block.get("text")]
        elements = block.get("elements")
        if isinstance(elements, list):
            values.extend(elements)
        if any(
            isinstance(value, dict) and text in str(value.get("text") or "") for value in values
        ):
            return True
    return False


async def _refresh_once(
    state: Mapping[str, Any], client: LangGraphClient
) -> tuple[Literal["updated", "pending", "unavailable"], str]:
    payload = _payload(state, 0)
    if payload is None:
        return "unavailable", "invalid payload"

    mapping = await lookup_slack_run_message_mapping(
        client, payload["channel_id"], payload["run_id"]
    )
    if not mapping or mapping.get("thread_ts") != payload["thread_ts"]:
        return "unavailable", "run message mapping unavailable"
    message_ts = mapping.get("message_ts")
    if not isinstance(message_ts, str) or not message_ts:
        return "unavailable", "run has no Slack response"

    try:
        snapshot = await get_langsmith_thread_cost(
            payload["agent_thread_id"], payload["prepare_run_id"]
        )
    except LangSmithCostUnavailable as exc:
        return "unavailable", str(exc)
    if snapshot is None:
        return "pending", "LangSmith trace or fresh aggregate unavailable"

    message = await fetch_slack_thread_message_by_ts(
        payload["channel_id"], payload["thread_ts"], message_ts
    )
    if message is None:
        return "pending", "Slack message unavailable"
    text = message.get("text")
    blocks = message.get("blocks")
    if not isinstance(text, str) or (blocks is not None and not isinstance(blocks, list)):
        return "unavailable", "invalid Slack message"

    updated_text, updated_blocks = with_slack_session_cost(text, blocks, snapshot.total_cost)
    cost_label = format_slack_session_cost(snapshot.total_cost)
    if cost_label not in updated_text or not _blocks_contain(updated_blocks, cost_label):
        return "unavailable", "Slack usage footer unavailable"
    if updated_text == text and updated_blocks == blocks:
        return "updated", "already current"

    updated, error = await update_slack_message(
        payload["channel_id"], message_ts, updated_text, blocks=updated_blocks
    )
    if not updated:
        return "pending", error or "Slack update failed"
    return "updated", "Slack footer updated"


async def run_session_cost_refresh(
    state: Mapping[str, Any], *, client: LangGraphClient | None = None
) -> dict[str, Any]:
    """Refresh the mapped Slack footer or enqueue the next bounded attempt."""
    client = client or langgraph_client()
    raw_attempt = state.get("attempt")
    attempt = (
        raw_attempt if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) else -1
    )
    if attempt < 0 or attempt >= len(_RETRY_DELAYS_SECONDS):
        return {"status": "unavailable", "reason": "invalid attempt"}

    try:
        status, reason = await _refresh_once(state, client)
    except Exception:  # noqa: BLE001
        logger.warning("Session-cost refresh attempt failed", exc_info=True)
        status, reason = "pending", "refresh attempt failed"
    if status != "pending":
        if status == "unavailable":
            logger.info(
                "Session-cost refresh unavailable for run %s: %s", state.get("run_id"), reason
            )
        return {"status": status, "reason": reason}
    next_attempt = attempt + 1
    if next_attempt >= len(_RETRY_DELAYS_SECONDS):
        logger.info("Session-cost refresh exhausted for run %s: %s", state.get("run_id"), reason)
        return {"status": "exhausted", "reason": reason}
    scheduled = await schedule_session_cost_refresh(state, attempt=next_attempt, client=client)
    return {
        "status": "retry_scheduled" if scheduled else "unavailable",
        "reason": reason,
        "attempt": next_attempt,
    }
