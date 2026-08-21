"""The stored mapping between a Slack location and Open SWE threads and runs.

Two namespaces back it: ``slack_thread_map`` binds a ``(channel, thread_ts)``
pair to exactly one Open SWE thread, and ``slack_run_map`` records which run
produced which Slack message so reactions and stop requests can find it again.

The binding is deliberately explicit rather than derived. A Slack thread that
was moved, or an Open SWE thread that was recreated, must keep answering in one
place; deriving the id from the location each time would silently fork the
conversation.
"""

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from langgraph_sdk.client import LangGraphClient

from ..thread_ids import slack_thread_id
from .format import is_slack_channel_id, is_slack_message_ts

logger = logging.getLogger(__name__)

_SLACK_THREAD_MAP_NAMESPACE = "slack_thread_map"
_SLACK_RUN_MAP_NAMESPACE = "slack_run_map"
_THREAD_RUN_KEY_PREFIX = "thread:"
_MESSAGE_RUN_KEY_PREFIX = "message:"
_RUN_MESSAGE_KEY_PREFIX = "run:"


class SlackThreadMappingError(RuntimeError):
    pass


def _normalize_slack_location(channel_id: str, thread_ts: str) -> tuple[str, str]:
    channel = channel_id.strip() if isinstance(channel_id, str) else ""
    timestamp = thread_ts.strip() if isinstance(thread_ts, str) else ""
    if not is_slack_channel_id(channel):
        raise SlackThreadMappingError("Invalid Slack channel ID")
    if not is_slack_message_ts(timestamp):
        raise SlackThreadMappingError("Invalid Slack thread timestamp")
    return channel, timestamp


def _mapping_thread_id(item: Mapping[str, Any] | None) -> str | None:
    if not item:
        return None
    value = item.get("value")
    if not isinstance(value, Mapping):
        return None
    thread_id = value.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


async def lookup_slack_thread_id(
    langgraph_client: LangGraphClient, channel_id: str, thread_ts: str
) -> str | None:
    """Look up the Open SWE thread explicitly mapped to a Slack location."""
    channel, timestamp = _normalize_slack_location(channel_id, thread_ts)
    item = await langgraph_client.store.get_item((_SLACK_THREAD_MAP_NAMESPACE, channel), timestamp)
    return _mapping_thread_id(item)


async def bind_slack_thread_id(
    langgraph_client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
    thread_id: str,
) -> str:
    """Persist an explicit Slack-location mapping without overwriting another thread."""
    channel, timestamp = _normalize_slack_location(channel_id, thread_ts)
    normalized_thread_id = thread_id.strip() if isinstance(thread_id, str) else ""
    if not normalized_thread_id:
        raise SlackThreadMappingError("Open SWE thread ID is required")
    existing = await lookup_slack_thread_id(langgraph_client, channel, timestamp)
    if existing and existing != normalized_thread_id:
        raise SlackThreadMappingError("Slack location is already mapped to another thread")
    await langgraph_client.store.put_item(
        (_SLACK_THREAD_MAP_NAMESPACE, channel),
        timestamp,
        {
            "thread_id": normalized_thread_id,
            "channel_id": channel,
            "thread_ts": timestamp,
        },
    )
    persisted = await lookup_slack_thread_id(langgraph_client, channel, timestamp)
    if persisted != normalized_thread_id:
        raise SlackThreadMappingError("Slack thread mapping did not persist")
    return normalized_thread_id


def _thread_metadata_slack_location(thread: Mapping[str, Any]) -> tuple[str, str] | None:
    metadata = thread.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    source_context = metadata.get("source_context")
    if not isinstance(source_context, Mapping):
        return None
    slack_thread = source_context.get("slack_thread")
    if not isinstance(slack_thread, Mapping):
        return None
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
        return None
    try:
        return _normalize_slack_location(channel_id, thread_ts)
    except SlackThreadMappingError:
        return None


async def resolve_slack_thread_id(
    langgraph_client: LangGraphClient, channel_id: str, thread_ts: str
) -> str:
    """Resolve or create the explicit Open SWE thread mapping for a Slack location."""
    channel, timestamp = _normalize_slack_location(channel_id, thread_ts)
    item = await langgraph_client.store.get_item((_SLACK_THREAD_MAP_NAMESPACE, channel), timestamp)
    existing = _mapping_thread_id(item)
    if existing:
        return existing
    value = item.get("value") if isinstance(item, Mapping) else None
    nonce = value.get("nonce") if isinstance(value, Mapping) else None

    matches = await langgraph_client.threads.search(
        metadata={
            "source_context": {"slack_thread": {"channel_id": channel, "thread_ts": timestamp}}
        },
        limit=2,
    )
    matching_ids = {
        candidate
        for thread in matches or []
        if isinstance(thread, Mapping)
        and _thread_metadata_slack_location(thread) == (channel, timestamp)
        and isinstance(candidate := thread.get("thread_id") or thread.get("id"), str)
        and candidate
    }
    if len(matching_ids) > 1:
        raise SlackThreadMappingError("Multiple Open SWE threads match this Slack location")

    candidate = next(
        iter(matching_ids),
        slack_thread_id(channel, timestamp, nonce),
    )
    await bind_slack_thread_id(langgraph_client, channel, timestamp, candidate)
    return candidate


async def get_active_slack_thread(
    langgraph_client: LangGraphClient,
    thread_id: str | None,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the active Slack location stored on an Open SWE thread."""
    if thread_id:
        try:
            thread = await langgraph_client.threads.get(thread_id)
            metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
            source_context = (
                metadata.get("source_context") if isinstance(metadata, Mapping) else None
            )
            slack_thread = (
                source_context.get("slack_thread") if isinstance(source_context, Mapping) else None
            )
            if isinstance(slack_thread, Mapping):
                location = dict(slack_thread)
                _normalize_slack_location(
                    str(location.get("channel_id") or ""), str(location.get("thread_ts") or "")
                )
                return location
        except Exception:
            logger.debug("Could not resolve active Slack location for thread %s", thread_id)
    if isinstance(fallback, Mapping):
        location = dict(fallback)
        try:
            _normalize_slack_location(
                str(location.get("channel_id") or ""), str(location.get("thread_ts") or "")
            )
        except SlackThreadMappingError:
            return None
        return location
    return None


async def delete_slack_thread_associations(
    langgraph_client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
    *,
    expected_thread_id: str | None = None,
) -> None:
    """Delete all Open SWE associations for a Slack location."""
    channel, timestamp = _normalize_slack_location(channel_id, thread_ts)
    mapped_thread_id = await lookup_slack_thread_id(langgraph_client, channel, timestamp)
    if expected_thread_id and mapped_thread_id and mapped_thread_id != expected_thread_id:
        return
    await langgraph_client.store.put_item(
        (_SLACK_THREAD_MAP_NAMESPACE, channel),
        timestamp,
        {"channel_id": channel, "thread_ts": timestamp, "nonce": str(uuid.uuid4())},
    )
    namespace = (_SLACK_RUN_MAP_NAMESPACE, channel)
    while True:
        response = await langgraph_client.store.search_items(
            namespace,
            filter={"thread_ts": timestamp},
            limit=100,
            offset=0,
        )
        items = response.get("items") if isinstance(response, Mapping) else None
        exact_items = [
            item
            for item in (items or [])
            if isinstance(item, Mapping)
            and item.get("namespace") in (list(namespace), namespace)
            and isinstance(item.get("value"), Mapping)
            and item["value"].get("thread_ts") == timestamp
            and isinstance(item.get("key"), str)
        ]
        if not exact_items:
            break
        for item in exact_items:
            await langgraph_client.store.delete_item(namespace, key=item["key"])
    if await lookup_slack_thread_id(langgraph_client, channel, timestamp):
        raise SlackThreadMappingError("Original Slack thread mapping was not detached")


def _extract_run_id_from_store_item(item: Mapping[str, Any] | None) -> str | None:
    if not item:
        return None
    value = item.get("value")
    if not isinstance(value, dict):
        return None
    run_id = value.get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


async def store_slack_run_mapping(
    langgraph_client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
    run_id: str,
    *,
    message_ts: str | None = None,
    triggering_user_id: str | None = None,
    trace_message_ts: str | None = None,
    agent_thread_id: str | None = None,
) -> None:
    """Persist Slack thread/message to LangGraph run mapping."""
    namespace = (_SLACK_RUN_MAP_NAMESPACE, channel_id)
    if not trace_message_ts:
        existing = await lookup_slack_thread_run_mapping(langgraph_client, channel_id, thread_ts)
        if isinstance(existing, dict):
            candidate = existing.get("trace_message_ts")
            if isinstance(candidate, str) and candidate:
                trace_message_ts = candidate
    value: dict[str, Any] = {"run_id": run_id, "thread_ts": thread_ts}
    if triggering_user_id:
        value["triggering_user_id"] = triggering_user_id
    if trace_message_ts:
        value["trace_message_ts"] = trace_message_ts
    if agent_thread_id:
        value["agent_thread_id"] = agent_thread_id
    try:
        await langgraph_client.store.put_item(
            namespace, f"{_THREAD_RUN_KEY_PREFIX}{thread_ts}", value
        )
        run_key = f"{_RUN_MESSAGE_KEY_PREFIX}{run_id}"
        existing_run = await langgraph_client.store.get_item(namespace, run_key)
        stored_run_value = existing_run.get("value") if isinstance(existing_run, dict) else None
        run_value = {
            **(stored_run_value if isinstance(stored_run_value, dict) else {}),
            **value,
            **({"message_ts": message_ts} if message_ts else {}),
        }
        await langgraph_client.store.put_item(namespace, run_key, run_value)
        if message_ts:
            await langgraph_client.store.put_item(
                namespace,
                f"{_MESSAGE_RUN_KEY_PREFIX}{message_ts}",
                run_value,
            )
    except Exception:
        logger.exception(
            "Failed to store Slack run mapping for channel=%s thread=%s run=%s",
            channel_id,
            thread_ts,
            run_id,
        )


async def store_slack_message_run_mapping(
    langgraph_client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    *,
    run_id: str | None = None,
    triggering_user_id: str | None = None,
) -> None:
    """Persist an exact run-to-Slack-message mapping."""
    namespace = (_SLACK_RUN_MAP_NAMESPACE, channel_id)
    try:
        thread_item = await langgraph_client.store.get_item(
            namespace, f"{_THREAD_RUN_KEY_PREFIX}{thread_ts}"
        )
        run_item = (
            await langgraph_client.store.get_item(namespace, f"{_RUN_MESSAGE_KEY_PREFIX}{run_id}")
            if run_id
            else thread_item
        )
        resolved_run_id = run_id or _extract_run_id_from_store_item(thread_item)
        if not resolved_run_id:
            logger.debug(
                "No Slack run mapping found for channel=%s thread=%s",
                channel_id,
                thread_ts,
            )
            return
        thread_value = thread_item.get("value") if isinstance(thread_item, dict) else None
        run_value = run_item.get("value") if isinstance(run_item, dict) else None
        value: dict[str, Any] = {
            **(thread_value if isinstance(thread_value, dict) else {}),
            **(run_value if isinstance(run_value, dict) else {}),
            "run_id": resolved_run_id,
            "thread_ts": thread_ts,
            "message_ts": message_ts,
        }
        if triggering_user_id:
            value["triggering_user_id"] = triggering_user_id
        await langgraph_client.store.put_item(
            namespace, f"{_MESSAGE_RUN_KEY_PREFIX}{message_ts}", value
        )
        await langgraph_client.store.put_item(
            namespace, f"{_RUN_MESSAGE_KEY_PREFIX}{resolved_run_id}", value
        )
    except Exception:
        logger.exception(
            "Failed to store Slack message run mapping for channel=%s message=%s",
            channel_id,
            message_ts,
        )


async def lookup_slack_run_message_mapping(
    langgraph_client: LangGraphClient,
    channel_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Return the latest Slack-message mapping written by an exact run."""
    namespace = (_SLACK_RUN_MAP_NAMESPACE, channel_id)
    try:
        item = await langgraph_client.store.get_item(
            namespace, f"{_RUN_MESSAGE_KEY_PREFIX}{run_id}"
        )
    except Exception:
        logger.exception(
            "Failed to look up Slack run mapping for channel=%s run=%s",
            channel_id,
            run_id,
        )
        return None
    value = item.get("value") if isinstance(item, dict) else None
    return value if isinstance(value, dict) else None


async def lookup_slack_thread_run_mapping(
    langgraph_client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
) -> dict[str, Any] | None:
    """Return the stored mapping value for a Slack thread, or None."""
    namespace = (_SLACK_RUN_MAP_NAMESPACE, channel_id)
    try:
        item = await langgraph_client.store.get_item(
            namespace, f"{_THREAD_RUN_KEY_PREFIX}{thread_ts}"
        )
    except Exception:
        logger.exception(
            "Failed to look up Slack thread run mapping for channel=%s thread=%s",
            channel_id,
            thread_ts,
        )
        return None
    if not item:
        return None
    value = item.get("value")
    return value if isinstance(value, dict) else None


async def lookup_slack_run_mapping(
    langgraph_client: LangGraphClient,
    channel_id: str,
    message_ts: str,
) -> dict[str, Any] | None:
    """Return the stored mapping value for a Slack bot message, or None."""
    namespace = (_SLACK_RUN_MAP_NAMESPACE, channel_id)
    try:
        item = await langgraph_client.store.get_item(
            namespace, f"{_MESSAGE_RUN_KEY_PREFIX}{message_ts}"
        )
    except Exception:
        logger.exception(
            "Failed to look up Slack message run mapping for channel=%s message=%s",
            channel_id,
            message_ts,
        )
        return None
    if not item:
        return None
    value = item.get("value")
    return value if isinstance(value, dict) else None
