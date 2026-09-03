"""Tool that schedules a one-shot re-trigger of the current agent thread."""

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from weakref import WeakValueDictionary
from xml.etree import ElementTree

from langchain_core.messages import BaseMessage
from langgraph_sdk import get_client
from pydantic import BaseModel

from agent.dispatch import COMPLETION_WEBHOOK_URL, prepare_run_config
from agent.input_messages import build_run_input
from agent.run_config import RunConfig
from agent.slack.client import get_active_slack_thread
from agent.utils.thread_ops import langgraph_url

logger = logging.getLogger(__name__)

_AGENT_ASSISTANT_ID = "agent"
_MIN_DELAY_SECONDS = 60
_MAX_DELAY_SECONDS = 86_400
_END_TIME_PADDING_SECONDS = 90

_WAKEUP_KIND = "thread_wakeup"
_WAKEUP_SENDER_ID = "system:thread-wakeup"
_MAX_WAKEUPS_BETWEEN_USER_MESSAGES = 10
_WAKEUP_GENERATION_METADATA_KEY = "thread_wakeup_generation"
_WAKEUP_COUNT_METADATA_KEY = "thread_wakeup_count"
_WAKEUP_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
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


def _message_content(message: object) -> tuple[object, str | None]:
    if isinstance(message, BaseMessage):
        return message.content, message.id
    if isinstance(message, dict):
        message_id = message.get("id")
        return message.get("content"), message_id if isinstance(message_id, str) else None
    return None, None


def _input_message_kind(content: object) -> str | None:
    values = content if isinstance(content, list) else [content]
    for value in values:
        text = value.get("text") if isinstance(value, dict) else value
        if not isinstance(text, str) or "<input-message" not in text:
            continue
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            continue
        messages = [root] if root.tag == "input-message" else root.findall(".//input-message")
        for message in messages:
            kind = message.get("kind")
            if kind in {"human", "system"}:
                return kind
    return None


def _latest_human_generation(messages: object) -> str:
    if not isinstance(messages, (list, tuple)):
        return "no-human-message"
    for index in range(len(messages) - 1, -1, -1):
        content, message_id = _message_content(messages[index])
        if _input_message_kind(content) != "human":
            continue
        identity = message_id or repr(content)
        return hashlib.sha256(f"{index}:{identity}".encode()).hexdigest()
    return "no-human-message"


def _wakeup_lock(thread_id: str) -> asyncio.Lock:
    lock = _WAKEUP_LOCKS.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _WAKEUP_LOCKS[thread_id] = lock
    return lock


async def _wakeup_budget(client: Any, thread_id: str) -> tuple[str, int]:
    state = await client.threads.get_state(thread_id)
    values = state.get("values") if isinstance(state, dict) else None
    messages = values.get("messages") if isinstance(values, dict) else None
    generation = _latest_human_generation(messages)

    thread = await client.threads.get(thread_id)
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get(_WAKEUP_GENERATION_METADATA_KEY) != generation:
        return generation, 0
    count = metadata.get(_WAKEUP_COUNT_METADATA_KEY)
    return generation, count if isinstance(count, int) and count >= 0 else 0


async def _record_wakeup(client: Any, thread_id: str, generation: str, count: int) -> None:
    await client.threads.update(
        thread_id=thread_id,
        metadata={
            _WAKEUP_GENERATION_METADATA_KEY: generation,
            _WAKEUP_COUNT_METADATA_KEY: count,
        },
    )


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
    client: Any | None = None,
) -> dict[str, Any]:
    resolved_client = get_client(url=langgraph_url()) if client is None else client
    schedule = _build_one_shot_cron(fire_time)
    end_time = fire_time + timedelta(seconds=_END_TIME_PADDING_SECONDS)
    run_config = prepare_run_config(
        {"configurable": configurable},
        {"kind": "thread_wakeup", "thread_id": thread_id},
    )
    kwargs: dict[str, Any] = {
        "schedule": schedule,
        "input": build_run_input(
            prompt,
            {
                "sender_id": _WAKEUP_SENDER_ID,
                "surface": "automation",
                "kind": "system",
            },
            systems=[
                {
                    "id": _WAKEUP_SENDER_ID,
                    "display_name": "Thread wakeup scheduler",
                    "platform": "open-swe",
                }
            ],
        ),
        "config": run_config,
        "end_time": end_time,
        "timezone": "UTC",
        "metadata": run_config["metadata"],
    }
    if COMPLETION_WEBHOOK_URL:
        kwargs["webhook"] = COMPLETION_WEBHOOK_URL
    cron = await resolved_client.crons.create_for_thread(
        thread_id,
        _AGENT_ASSISTANT_ID,
        **kwargs,
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

    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}

    client = get_client(url=langgraph_url())
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
        value = cfg.get(key)
        if value is not None:
            if isinstance(value, BaseModel):
                value = value.model_dump(mode="json", exclude_unset=True)
            wakeup_configurable[key] = value
    active = await get_active_slack_thread(
        client,
        thread_id,
        cfg.slack_thread.dump() if cfg.slack_thread else None,
    )
    if active:
        wakeup_configurable["slack_thread"] = active

    await _purge_expired_wakeups_best_effort()

    async with _wakeup_lock(thread_id):
        try:
            generation, wakeup_count = await _wakeup_budget(client, thread_id)
        except Exception:
            logger.exception("Failed to verify thread wakeup budget for %s", thread_id)
            return {"success": False, "error": "Unable to verify the thread wakeup limit"}
        if wakeup_count >= _MAX_WAKEUPS_BETWEEN_USER_MESSAGES:
            return {
                "success": False,
                "error": (
                    f"Thread wakeup limit reached: at most "
                    f"{_MAX_WAKEUPS_BETWEEN_USER_MESSAGES} wakeups may be scheduled between "
                    "user messages. Wait for a new user message before scheduling another."
                ),
            }
        try:
            await _record_wakeup(client, thread_id, generation, wakeup_count + 1)
        except Exception:
            logger.exception("Failed to record thread wakeup budget for %s", thread_id)
            return {"success": False, "error": "Unable to record the thread wakeup limit"}
        try:
            return await _create_wakeup_cron(
                thread_id=thread_id,
                fire_time=fire_time,
                prompt=wakeup_prompt,
                configurable=wakeup_configurable,
                client=client,
            )
        except Exception as exc:
            logger.exception("Failed to schedule thread wakeup for %s", thread_id)
            return {"success": False, "error": str(exc)}
