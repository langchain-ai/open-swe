"""Slack reaction feedback handling."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from .langsmith import create_langsmith_feedback, delete_langsmith_feedback
from .slack import lookup_slack_run_mapping

logger = logging.getLogger(__name__)

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

FEEDBACK_REACTIONS: dict[str, float] = {
    "+1": 1.0,
    "thumbsup": 1.0,
    "-1": 0.0,
    "thumbsdown": 0.0,
}

_REACTION_STATE_NAMESPACE = "slack_reaction_state"
_REACTION_EVENT_NAMESPACE = "slack_reaction_events"


def _read_active_reactions(item: dict[str, Any] | None) -> set[str]:
    if not item:
        return set()
    value = item.get("value")
    if not isinstance(value, dict):
        return set()
    reactions = value.get("reactions")
    if not isinstance(reactions, list):
        return set()
    return {reaction for reaction in reactions if isinstance(reaction, str)}


def _feedback_key(channel_id: str, user_id: str, message_ts: str) -> str:
    return f"slack_reaction:{channel_id}:{user_id}:{message_ts}"


def _reaction_state_key(run_id: str, user_id: str, message_ts: str) -> str:
    return f"{run_id}:{user_id}:{message_ts}"


async def _event_was_processed(
    langgraph_client: LangGraphClient, channel_id: str, event_id: str
) -> bool:
    if not event_id:
        return False
    item = await langgraph_client.store.get_item((_REACTION_EVENT_NAMESPACE, channel_id), event_id)
    return bool(item)


async def _mark_event_processed(
    langgraph_client: LangGraphClient, channel_id: str, event_id: str
) -> None:
    if not event_id:
        return
    await langgraph_client.store.put_item(
        (_REACTION_EVENT_NAMESPACE, channel_id), event_id, {"event_id": event_id}
    )


async def _update_reaction_state(
    langgraph_client: LangGraphClient,
    *,
    channel_id: str,
    run_id: str,
    user_id: str,
    message_ts: str,
    reaction: str,
    added: bool,
) -> set[str]:
    namespace = (_REACTION_STATE_NAMESPACE, channel_id)
    key = _reaction_state_key(run_id, user_id, message_ts)
    item = await langgraph_client.store.get_item(namespace, key)
    active_reactions = _read_active_reactions(item)

    if added:
        active_reactions.add(reaction)
    else:
        active_reactions.discard(reaction)

    await langgraph_client.store.put_item(
        namespace,
        key,
        {
            "run_id": run_id,
            "user_id": user_id,
            "message_ts": message_ts,
            "reactions": sorted(active_reactions),
        },
    )
    return active_reactions


def _score_reactions(reactions: set[str]) -> float | None:
    scores = {
        FEEDBACK_REACTIONS[reaction] for reaction in reactions if reaction in FEEDBACK_REACTIONS
    }
    if not scores:
        return None
    if len(scores) > 1:
        # Conflicting positive + negative reactions from the same user — treat
        # as ambiguous and clear feedback rather than recording a misleading
        # average score.
        return None
    return next(iter(scores))


async def process_slack_reaction(
    event: dict[str, Any],
    *,
    event_id: str = "",
    added: bool,
) -> None:
    reaction = event.get("reaction")
    if not isinstance(reaction, str) or reaction not in FEEDBACK_REACTIONS:
        return

    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "message":
        return

    channel_id = item.get("channel")
    message_ts = item.get("ts")
    user_id = event.get("user")
    if not (
        isinstance(channel_id, str)
        and channel_id
        and isinstance(message_ts, str)
        and message_ts
        and isinstance(user_id, str)
        and user_id
    ):
        return

    langgraph_client = get_client(url=LANGGRAPH_URL)
    if await _event_was_processed(langgraph_client, channel_id, event_id):
        return

    mapping = await lookup_slack_run_mapping(langgraph_client, channel_id, message_ts)
    if not mapping:
        logger.debug(
            "No run mapping for Slack reaction on channel=%s message=%s",
            channel_id,
            message_ts,
        )
        return
    run_id_value = mapping.get("run_id")
    if not isinstance(run_id_value, str) or not run_id_value:
        return
    run_id = run_id_value

    triggering_user_id = mapping.get("triggering_user_id")
    if isinstance(triggering_user_id, str) and triggering_user_id and triggering_user_id != user_id:
        # Only the user who triggered the run may give feedback on it. Other
        # reactors are ignored to keep eval signal clean in shared channels.
        logger.debug(
            "Ignoring Slack reaction from non-triggering user=%s on run=%s",
            user_id,
            run_id,
        )
        return

    active_reactions = await _update_reaction_state(
        langgraph_client,
        channel_id=channel_id,
        run_id=run_id,
        user_id=user_id,
        message_ts=message_ts,
        reaction=reaction,
        added=added,
    )

    key = _feedback_key(channel_id, user_id, message_ts)
    source_info = {
        "source": "slack_reaction",
        "channel_id": channel_id,
        "message_ts": message_ts,
        "user_id": user_id,
    }
    score = _score_reactions(active_reactions)
    if score is None:
        success = await asyncio.to_thread(delete_langsmith_feedback, run_id, key)
    else:
        success = await asyncio.to_thread(
            create_langsmith_feedback,
            run_id,
            key,
            score=score,
            comment=f"Slack reaction feedback from user {user_id}",
            source_info={**source_info, "reactions": sorted(active_reactions)},
        )

    if success:
        await _mark_event_processed(langgraph_client, channel_id, event_id)


async def process_slack_reaction_added(event: dict[str, Any], event_id: str = "") -> None:
    await process_slack_reaction(event, event_id=event_id, added=True)


async def process_slack_reaction_removed(event: dict[str, Any], event_id: str = "") -> None:
    await process_slack_reaction(event, event_id=event_id, added=False)
