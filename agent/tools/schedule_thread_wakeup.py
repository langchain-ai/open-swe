"""Tool that schedules a one-shot re-trigger of the current agent thread."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

from ..utils.thread_ops import langgraph_url

logger = logging.getLogger(__name__)

_AGENT_ASSISTANT_ID = "agent"
_MIN_DELAY_SECONDS = 60
_MAX_DELAY_SECONDS = 86_400
_END_TIME_PADDING_SECONDS = 90
_MAX_WAKEUP_TICKS_PER_THREAD = 3
_WAKEUP_CRON_KIND = "thread_wakeup"
_WAKEUP_SEARCH_LIMIT = 100

_DEFAULT_WAKEUP_PROMPT = (
    "This is an automated re-trigger of this thread. Before doing any new "
    "work, count how many prior wakeup turns this thread already has — if "
    "you have already polled 3+ times for the same target (same PR, same "
    "branch, same deploy), STOP scheduling further wakeups, report status "
    "to the source channel, and exit. Otherwise check the current state of "
    "what you were waiting on and continue. Do not re-clone, re-read "
    "AGENTS.md, or re-derive task context that an earlier turn already "
    "established in this thread."
)


def _ceil_to_next_minute(value: datetime) -> datetime:
    """Round a datetime up to the next whole minute."""
    rounded = value.replace(second=0, microsecond=0)
    if rounded == value:
        return rounded
    return rounded + timedelta(minutes=1)


def _build_one_shot_cron(fire_time: datetime) -> str:
    """Build a 5-field cron expression that fires at ``fire_time`` (UTC)."""
    return " ".join(
        [
            str(fire_time.minute),
            str(fire_time.hour),
            str(fire_time.day),
            str(fire_time.month),
            "*",
        ]
    )


async def _count_thread_wakeup_crons(thread_id: str) -> int:
    """Return how many wakeup crons already exist for this thread."""
    client = get_client(url=langgraph_url())
    crons = await client.crons.search(
        thread_id=thread_id,
        metadata={"kind": _WAKEUP_CRON_KIND},
        limit=_WAKEUP_SEARCH_LIMIT,
    )
    return len(crons or [])


async def _create_wakeup_cron(
    *,
    thread_id: str,
    fire_time: datetime,
    prompt: str,
    configurable: dict[str, Any],
) -> dict[str, Any]:
    client = get_client(url=langgraph_url())
    schedule = _build_one_shot_cron(fire_time)
    end_time = fire_time + timedelta(seconds=_END_TIME_PADDING_SECONDS)
    run_config: dict[str, Any] = {"configurable": configurable}
    cron = await client.crons.create_for_thread(
        thread_id,
        _AGENT_ASSISTANT_ID,
        schedule=schedule,
        input={"messages": [{"role": "user", "content": prompt}]},
        config=run_config,
        end_time=end_time,
        timezone="UTC",
        metadata={
            "kind": _WAKEUP_CRON_KIND,
            "thread_id": thread_id,
        },
    )
    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    return {
        "success": True,
        "cron_id": cron_id,
        "scheduled_for": fire_time.isoformat(),
        "thread_id": thread_id,
    }


async def schedule_thread_wakeup(delay_minutes: int, prompt: str | None = None) -> dict[str, Any]:
    """Schedule a one-shot re-trigger of the current thread after a delay.

    Use this when you need to poll or check back on something later — e.g.
    waiting for CI to finish, a deploy to complete, or an external process
    to settle. The current thread will be re-invoked with the given prompt
    (or a default wakeup message) after the specified delay.

    Args:
        delay_minutes: How many minutes from now to wait before re-triggering.
            Minimum 1 minute, maximum 1440 (24 hours).
        prompt: Optional message to send to the thread when it wakes up.
            If omitted, a default polling prompt is used.

    Returns a dict with ``success``, ``cron_id``, ``scheduled_for`` (ISO UTC),
    and ``thread_id``.
    """
    if not isinstance(delay_minutes, int) or delay_minutes < 1:
        return {"success": False, "error": "delay_minutes must be a positive integer (>= 1)"}
    delay_seconds = delay_minutes * 60
    if delay_seconds < _MIN_DELAY_SECONDS:
        return {"success": False, "error": "delay must be at least 1 minute"}
    if delay_seconds > _MAX_DELAY_SECONDS:
        return {"success": False, "error": "delay must be at most 1440 minutes (24 hours)"}

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}

    try:
        prior_wakeup_count = await _count_thread_wakeup_crons(thread_id)
    except Exception as exc:
        logger.exception("Failed to count prior wakeup crons for %s", thread_id)
        return {"success": False, "error": str(exc)}
    if prior_wakeup_count >= _MAX_WAKEUP_TICKS_PER_THREAD:
        return {
            "success": False,
            "error": (
                f"thread {thread_id} has already scheduled {prior_wakeup_count} "
                f"wakeup ticks (cap is {_MAX_WAKEUP_TICKS_PER_THREAD}); refusing "
                "to re-arm. Report status to the source channel and exit; if "
                "polling must continue, ask the user to open a new thread."
            ),
        }

    fire_time = _ceil_to_next_minute(datetime.now(UTC) + timedelta(seconds=delay_seconds))
    wakeup_prompt = (
        prompt.strip() if isinstance(prompt, str) and prompt.strip() else _DEFAULT_WAKEUP_PROMPT
    )

    passthrough_keys = (
        "repo",
        "source",
        "slack_thread",
        "linear_issue",
        "github_login",
        "user_email",
        "schedule_id",
    )
    wakeup_configurable: dict[str, Any] = {"thread_id": thread_id}
    for key in passthrough_keys:
        value = configurable.get(key)
        if value is not None:
            wakeup_configurable[key] = value

    try:
        return await _create_wakeup_cron(
            thread_id=thread_id,
            fire_time=fire_time,
            prompt=wakeup_prompt,
            configurable=wakeup_configurable,
        )
    except Exception as exc:
        logger.exception("Failed to schedule thread wakeup for %s", thread_id)
        return {"success": False, "error": str(exc)}
