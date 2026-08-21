"""Slack emergency-stop reaction handling."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk.client import LangGraphClient

from ..config import agent_version_metadata, langgraph_client
from ..dispatch import dispatch_agent_run
from ..utils.slack_threads import (
    lookup_slack_run_mapping,
    lookup_slack_thread_id,
    store_slack_run_mapping,
)
from .slack_events import claim_slack_event

logger = logging.getLogger(__name__)

_QUEUE_RECORDS = (
    (("queue",), "pending_messages"),
    (("autofix",), "pending_event"),
)


def _mapping_value(value: object, key: str) -> object:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _thread_metadata(thread: object) -> dict[str, Any]:
    metadata = _mapping_value(thread, "metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _matching_slack_context(
    metadata: Mapping[str, Any], channel_id: str, thread_ts: str
) -> dict[str, Any] | None:
    source_context = metadata.get("source_context")
    if not isinstance(source_context, Mapping):
        return None
    slack_thread = source_context.get("slack_thread")
    if not isinstance(slack_thread, Mapping):
        return None
    if slack_thread.get("channel_id") != channel_id or slack_thread.get("thread_ts") != thread_ts:
        return None
    return dict(slack_thread)


async def _resolve_stop_target(
    client: LangGraphClient, channel_id: str, message_ts: str
) -> tuple[str, str, dict[str, Any], dict[str, Any]] | None:
    mapping = await lookup_slack_run_mapping(client, channel_id, message_ts)
    if mapping is None:
        thread_ts = message_ts
    else:
        mapped_thread_ts = mapping.get("thread_ts")
        if not isinstance(mapped_thread_ts, str) or not mapped_thread_ts:
            return None
        thread_ts = mapped_thread_ts

    thread_id = await lookup_slack_thread_id(client, channel_id, thread_ts)
    if not thread_id:
        return None
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        logger.debug(
            "Ignoring Slack stop reaction without a matching thread: channel=%s message=%s",
            channel_id,
            message_ts,
        )
        return None

    metadata = _thread_metadata(thread)
    slack_thread = _matching_slack_context(metadata, channel_id, thread_ts)
    if slack_thread is None:
        logger.warning(
            "Ignoring Slack stop reaction with mismatched thread metadata: thread=%s",
            thread_id,
        )
        return None
    return thread_id, thread_ts, metadata, slack_thread


async def _active_run_ids(client: LangGraphClient, thread_id: str) -> list[str]:
    run_ids: set[str] = set()
    for status in ("pending", "running"):
        offset = 0
        while True:
            runs = await client.runs.list(thread_id, status=status, limit=100, offset=offset)
            for run in runs:
                run_id = _mapping_value(run, "run_id") or _mapping_value(run, "id")
                if isinstance(run_id, str) and run_id:
                    run_ids.add(run_id)
            if len(runs) < 100:
                break
            offset += len(runs)
    return sorted(run_ids)


async def _clear_deferred_work(client: LangGraphClient, thread_id: str) -> None:
    for namespace_prefix, key in _QUEUE_RECORDS:
        await client.store.delete_item((*namespace_prefix, thread_id), key)


def _summary_configurable(
    metadata: Mapping[str, Any], slack_thread: Mapping[str, Any]
) -> dict[str, Any]:
    source = metadata.get("source")
    configurable: dict[str, Any] = {
        "source": source if source in {"slack", "schedule"} else "slack",
        "slack_thread": dict(slack_thread),
        "plan_mode": False,
        "stop_summary": True,
    }

    repo = metadata.get("repo")
    if isinstance(repo, Mapping) and repo.get("owner") and repo.get("name"):
        configurable["repo"] = dict(repo)
    else:
        owner = metadata.get("repo_owner")
        name = metadata.get("repo_name")
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            configurable["repo"] = {"owner": owner, "name": name}

    for metadata_key, config_key in (
        ("github_login", "github_login"),
        ("triggering_user_email", "user_email"),
        ("environment", "environment"),
    ):
        value = metadata.get(metadata_key)
        if isinstance(value, str) and value:
            configurable[config_key] = value
    return configurable


def _stop_summary_prompt(had_active_runs: bool) -> str:
    observed_state = (
        "One or more active runs were interrupted before this turn started."
        if had_active_runs
        else "No active run was present when the stop reaction was processed."
    )
    return f"""This is an internal stop-summary turn triggered by a Slack :x: reaction, not a new task request. {observed_state}

Do not resume or continue the prior task. Do not modify files, run mutating commands, commit, push, open or update a pull request, or take any other implementation action. Inspect only the existing conversation and current sandbox state with read-only tools as needed.

Your first and only user-facing action must be one concise `slack_thread_reply` that factually summarizes what was completed, what was in progress when interrupted, and what remains. If no active run existed, say so. Do not post an acknowledgement before the summary. End immediately after posting it."""


async def _process_slack_stop_reaction(event: dict[str, Any], event_id: str) -> None:
    if event.get("reaction") != "x":
        return
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "message":
        return
    channel_id = item.get("channel")
    message_ts = item.get("ts")
    if not (
        isinstance(channel_id, str) and channel_id and isinstance(message_ts, str) and message_ts
    ):
        return
    if not event_id:
        logger.warning("Ignoring Slack stop reaction without an event id")
        return

    client = langgraph_client()
    target = await _resolve_stop_target(client, channel_id, message_ts)
    if target is None:
        return
    if not await claim_slack_event(event_id):
        return

    thread_id, thread_ts, metadata, slack_thread = target
    run_ids = await _active_run_ids(client, thread_id)
    if run_ids:
        await client.runs.cancel_many(
            thread_id=thread_id,
            run_ids=run_ids,
            action="interrupt",
        )
    await _clear_deferred_work(client, thread_id)
    await client.threads.update(
        thread_id=thread_id,
        metadata={
            "latest_run_status": "interrupted",
            "stop_requested_at_ms": int(datetime.now(UTC).timestamp() * 1000),
        },
    )

    configurable = _summary_configurable(metadata, slack_thread)
    summary_run = await dispatch_agent_run(
        thread_id,
        _stop_summary_prompt(bool(run_ids)),
        configurable,
        source=str(configurable["source"]),
        metadata=agent_version_metadata(),
        client=client,
    )
    summary_run_id = _mapping_value(summary_run, "run_id") or _mapping_value(summary_run, "id")
    if isinstance(summary_run_id, str) and summary_run_id:
        triggering_user_id = slack_thread.get("triggering_user_id")
        await store_slack_run_mapping(
            client,
            channel_id,
            thread_ts,
            summary_run_id,
            triggering_user_id=(
                triggering_user_id
                if isinstance(triggering_user_id, str) and triggering_user_id
                else None
            ),
        )


async def process_slack_stop_reaction(event: dict[str, Any], event_id: str = "") -> None:
    try:
        await _process_slack_stop_reaction(event, event_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to stop Open SWE from Slack reaction")
