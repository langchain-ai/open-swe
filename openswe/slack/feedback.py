"""Slack reaction feedback handling."""

import logging
from datetime import timedelta
from typing import Any

from openswe.event_claims import claim, held
from openswe.slack.records import SlackMessage, lookup_slack_run_mapping
from openswe.utils.langsmith import create_langsmith_feedback, delete_langsmith_feedback
from openswe.utils.reviewer_outcomes import outcome_from_score as _outcome_from_score
from openswe.utils.reviewer_outcomes import upsert_run_outcome

logger = logging.getLogger(__name__)


FEEDBACK_REACTIONS: dict[str, float] = {
    "+1": 1.0,
    "thumbsup": 1.0,
    "-1": 0.0,
    "thumbsdown": 0.0,
}

# Slack retries a delivery for minutes at most.
_REACTION_EVENT_TTL = timedelta(days=1)


def _feedback_key(channel_id: str, user_id: str, message_ts: str) -> str:
    return f"slack_reaction:{channel_id}:{user_id}:{message_ts}"


def _event_claim_key(channel_id: str, event_id: str) -> str:
    return f"{channel_id}:{event_id}"


async def _event_was_processed(channel_id: str, event_id: str) -> bool:
    return bool(event_id) and await held(
        "slack_reaction_event", _event_claim_key(channel_id, event_id)
    )


async def _mark_event_processed(channel_id: str, event_id: str) -> None:
    if event_id:
        await claim(
            "slack_reaction_event",
            _event_claim_key(channel_id, event_id),
            ttl=_REACTION_EVENT_TTL,
        )


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

    if await _event_was_processed(channel_id, event_id):
        return

    mapping = await lookup_slack_run_mapping(channel_id, message_ts)
    if mapping is None:
        logger.debug(
            "No run mapping for Slack reaction on channel=%s message=%s",
            channel_id,
            message_ts,
        )
        return
    run_id = mapping.run_id
    if run_id is None:
        return

    triggering_user_id = mapping.triggering_user_id
    if triggering_user_id and triggering_user_id != user_id:
        # Only the user who triggered the run may give feedback on it. Other
        # reactors are ignored to keep eval signal clean in shared channels.
        logger.debug(
            "Ignoring Slack reaction from non-triggering user=%s on run=%s",
            user_id,
            run_id,
        )
        return

    active_reactions = await SlackMessage.toggle_reaction(
        channel_id, message_ts, user_id, reaction, added=added
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
        success = await delete_langsmith_feedback(run_id, key)
    else:
        success = await create_langsmith_feedback(
            run_id,
            key,
            score=score,
            comment=f"Slack reaction feedback from user {user_id}",
            source_info={**source_info, "reactions": sorted(active_reactions)},
        )

    outcome = _outcome_from_score(score, source="slack")
    if outcome is not None:
        label, label_source = outcome
        await upsert_run_outcome(
            label=label,
            label_source=label_source,
            run_id=run_id,
            extra={"channel_id": channel_id},
        )

    if success:
        await _mark_event_processed(channel_id, event_id)


async def process_slack_reaction_added(event: dict[str, Any], event_id: str = "") -> None:
    await process_slack_reaction(event, event_id=event_id, added=True)


async def process_slack_reaction_removed(event: dict[str, Any], event_id: str = "") -> None:
    await process_slack_reaction(event, event_id=event_id, added=False)
