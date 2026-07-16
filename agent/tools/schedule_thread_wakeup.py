"""Tool that schedules a one-shot re-trigger of the current agent thread."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client
from langgraph_sdk.schema import Config

from ..utils.thread_ops import langgraph_url

logger = logging.getLogger(__name__)

_AGENT_ASSISTANT_ID = "agent"
_MIN_DELAY_SECONDS = 60
_MAX_DELAY_SECONDS = 86_400
_END_TIME_PADDING_SECONDS = 90

_WAKEUP_KIND = "thread_wakeup"
_PURGE_PAGE_SIZE = 100

_DEFAULT_WAKEUP_PROMPT = (
    "This is an automated re-trigger of this thread. The agent scheduled this "
    "wakeup to poll for updates. Check the current state of whatever you were "
    "waiting on and continue from there."
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


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def find_expired_wakeup_cron_ids(client: Any, *, now: datetime) -> list[str]:
    """Return the ids of ``thread_wakeup`` crons whose ``end_time`` has passed.

    Conservative: matches solely on ``metadata.kind == "thread_wakeup"`` AND a
    past ``end_time``, so analyzer/dashboard crons are never selected. Paginates
    fully before returning so the result is stable to delete afterwards.
    """
    expired_ids: list[str] = []
    offset = 0
    while True:
        page = await client.crons.search(
            metadata={"kind": _WAKEUP_KIND},
            limit=_PURGE_PAGE_SIZE,
            offset=offset,
        )
        if not page:
            break
        for cron in page:
            if not isinstance(cron, dict):
                continue
            end_time = _parse_iso(cron.get("end_time"))
            cron_id = cron.get("cron_id")
            if end_time is not None and end_time < now and isinstance(cron_id, str) and cron_id:
                expired_ids.append(cron_id)
        if len(page) < _PURGE_PAGE_SIZE:
            break
        offset += len(page)
    return expired_ids


async def purge_expired_wakeup_crons(client: Any, *, now: datetime) -> int:
    """Delete ``thread_wakeup`` crons whose ``end_time`` has already passed.

    Each wakeup is a thread-bound cron with an ``end_time`` (~90s past its fire)
    that stops it re-firing, but the cron row itself is never removed, so dead
    rows accumulate. This deletes only those dead rows. Returns the count deleted.
    """
    expired_ids = await find_expired_wakeup_cron_ids(client, now=now)
    deleted = 0
    for cron_id in expired_ids:
        await client.crons.delete(cron_id)
        deleted += 1
    return deleted


async def _purge_expired_wakeups_best_effort() -> None:
    """Opportunistically purge expired wakeup crons; never raises."""
    try:
        client = get_client(url=langgraph_url())
        deleted = await purge_expired_wakeup_crons(client, now=datetime.now(UTC))
        if deleted:
            logger.info("Purged %d expired thread_wakeup cron(s)", deleted)
    except Exception:
        logger.warning("Failed to purge expired thread_wakeup crons", exc_info=True)


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
    run_config: Config = {"configurable": configurable}
    cron = await client.crons.create_for_thread(
        thread_id,
        _AGENT_ASSISTANT_ID,
        schedule=schedule,
        input={"messages": [{"role": "user", "content": prompt}]},
        config=run_config,
        end_time=end_time,
        timezone="UTC",
        metadata={
            "kind": "thread_wakeup",
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

    await _purge_expired_wakeups_best_effort()

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
