"""Slack emergency-stop reaction handling."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import partial
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from openswe.config import ENV
from openswe.dispatch import dispatch_agent_run, thread_workspace
from openswe.message_queue import QueuedMessage
from openswe.prompts import prompt
from openswe.slack.client import (
    lookup_slack_run_mapping,
    lookup_slack_thread_id,
    store_slack_run_mapping,
)
from openswe.slack.code_channels import CODE_CHANNEL_SESSION_TS, set_session_status
from openswe.slack.events import claim_slack_event
from openswe.source_context import SourceContext

logger = logging.getLogger(__name__)

LANGGRAPH_URL = ENV.LANGGRAPH_URL.get()
_QUEUE_RECORDS = ((("autofix",), "pending_event"),)


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
    slack_thread = SourceContext.from_metadata(metadata).slack_thread
    if slack_thread is None or not slack_thread.is_at(channel_id, thread_ts):
        return None
    return slack_thread.model_dump(mode="json", exclude_unset=True)


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
    await QueuedMessage.clear(thread_id)
    for namespace_prefix, key in _QUEUE_RECORDS:
        await client.store.delete_item((*namespace_prefix, thread_id), key)


def _summary_configurable(
    metadata: Mapping[str, Any], slack_thread: Mapping[str, Any]
) -> dict[str, Any]:
    source = metadata.get("source")
    configurable: dict[str, Any] = {
        "source": source if source in {"slack", "schedule"} else "slack",
        "slack_thread": dict(slack_thread),
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
    ):
        value = metadata.get(metadata_key)
        if isinstance(value, str) and value:
            configurable[config_key] = value

    workspace = thread_workspace(metadata)
    if workspace is not None:
        configurable["workspace"] = workspace
        configurable["environment"] = workspace
    return configurable


def _stop_summary_prompt(had_active_runs: bool) -> str:
    observed_state = (
        "One or more active runs were interrupted before this turn started."
        if had_active_runs
        else "No active run was present when the stop reaction was processed."
    )
    return prompt("runs/slack-stop-summary", observed_state=observed_state)


def _agent_version_metadata() -> dict[str, str]:
    revision = ENV.LANGCHAIN_REVISION_ID.optional()
    return {"LANGSMITH_AGENT_VERSION": revision} if revision else {}


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

    client = get_client(url=LANGGRAPH_URL)
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
        thread_title=None,
        metadata=_agent_version_metadata(),
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


async def _process_agent_session_stopped(
    event: dict[str, object], event_id: str, team_id: str
) -> None:
    channel_id = event.get("channel") or event.get("channel_id")
    thread_ts = event.get("thread_ts", CODE_CHANNEL_SESSION_TS)
    if not isinstance(channel_id, str) or not channel_id:
        return
    if not isinstance(thread_ts, str) or not thread_ts:
        return
    client = get_client(url=LANGGRAPH_URL)
    thread_id = await lookup_slack_thread_id(client, channel_id, thread_ts)
    if not thread_id:
        return
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        logger.debug("Ignoring session stop for unknown thread %s", thread_id)
        return
    slack_thread = SourceContext.from_metadata(_thread_metadata(thread)).slack_thread
    if slack_thread is None or not slack_thread.is_at(channel_id, thread_ts):
        logger.warning(
            "Ignoring session stop with mismatched thread metadata",
            extra={"agent_thread_id": thread_id},
        )
        return
    if thread_ts != CODE_CHANNEL_SESSION_TS:
        from openswe.slack.client import post_slack_thread_reply
        from openswe.slack.http import SlackClient, slack_identity
        from openswe.slack.thinking import release_slack_location_status
        from openswe.threads.handlers import cancel_dashboard_thread
        from openswe.users.models import User

        user_id = event.get("user")
        if not event_id or not team_id:
            logger.warning("Ignoring session stop without an event or Slack workspace")
            return
        expected_team_id = slack_thread.team_id
        if not expected_team_id:
            async with SlackClient.bot() as slack:
                expected_team_id = (await slack_identity(slack))["team_id"]
        if expected_team_id != team_id:
            logger.warning("Ignoring session stop from a different Slack workspace")
            return
        if not isinstance(user_id, str) or not user_id:
            return
        user = await User.for_identity("slack", user_id)
        if user is None or not user.github_login:
            logger.warning("Ignoring session stop from an unlinked Slack user")
            return
        if not await claim_slack_event(event_id):
            return
        await cancel_dashboard_thread(
            thread_id,
            user.github_login,
            email=user.email,
            on_interrupted=partial(release_slack_location_status, channel_id, thread_ts),
        )
        await post_slack_thread_reply(
            channel_id,
            thread_ts,
            "Stop requested. Queued follow-ups may still run; you can continue in this thread.",
        )
        return
    if event_id and not await claim_slack_event(event_id):
        return

    run_ids = await _active_run_ids(client, thread_id)
    if run_ids:
        await client.runs.cancel_many(thread_id=thread_id, run_ids=run_ids, action="interrupt")
    await _clear_deferred_work(client, thread_id)
    await client.threads.update(
        thread_id=thread_id,
        metadata={
            "latest_run_status": "interrupted",
            "stop_requested_at_ms": int(datetime.now(UTC).timestamp() * 1000),
        },
    )
    await set_session_status(channel_id, "active")


async def process_agent_session_stopped(
    event: dict[str, object], event_id: str = "", team_id: str = ""
) -> None:
    """Stop work immediately when Slack signals the session was stopped."""
    try:
        await _process_agent_session_stopped(event, event_id, team_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to stop Open SWE from a Slack session stop event")
