"""Private Slack ratings and comments on completed Open SWE threads."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import BackgroundTasks
from pydantic import BaseModel, Field

from agent.slack.client import (
    get_slack_channel_context,
    lookup_slack_run_message_mapping,
    open_slack_modal,
    post_slack_ephemeral_message,
    slack_channel_allows_operations,
    slack_thread_mutation_lock,
)
from agent.slack.responses import FeedbackResponse
from agent.store import TypedStore
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.langsmith import create_langsmith_thread_feedback
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_RATE_PREFIX = "open_swe_feedback_rate_"
_COMMENT_ACTION = "open_swe_feedback_comment"
_COMMENT_BLOCK = "feedback_comment"
_RATINGS = ("😡 Very Bad", "🙁 Bad", "😐 Okay", "🙂 Good", "😍 Great")


class ThreadFeedback(BaseModel):
    agent_thread_id: str
    run_id: str
    channel_id: str
    thread_ts: str
    message_ts: str
    user_id: str
    prompted: bool = False
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str = Field(default="", max_length=3000)
    last_rating_ts: Decimal = Decimal(0)


def _store(channel_id: str) -> TypedStore[ThreadFeedback]:
    return TypedStore(("slack_thread_feedback", channel_id), ThreadFeedback)


@asynccontextmanager
async def _locked_feedback(
    record: ThreadFeedback, *, purpose: str = "feedback"
) -> AsyncIterator[ThreadFeedback | None]:
    async with slack_thread_mutation_lock(
        langgraph_client(), record.channel_id, record.message_ts, purpose=purpose
    ):
        yield await _store(record.channel_id).get(record.run_id)


def rating_blocks(run_id: str, thread_id: str) -> list[dict[str, Any]]:
    url = dashboard_thread_url(thread_id)
    thread_link = f"<{url}|this thread>" if url else "this thread"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"How did Open SWE do on {thread_link}?",
            },
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Add comments"},
                "action_id": _COMMENT_ACTION,
                "value": run_id,
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": label, "emoji": True},
                    "action_id": f"{_RATE_PREFIX}{rating}",
                    "value": run_id,
                }
                for rating, label in enumerate(_RATINGS, start=1)
            ],
        },
    ]


async def post_slack_feedback_prompt(
    thread_id: str, run_id: str, channel_id: str, *, require_answer: bool = False
) -> None:
    """Prompt the run's requester once, using its exact response mapping."""
    try:
        store = _store(channel_id)
        record = await store.get(run_id)
        if record is not None and record.prompted:
            return
        if record is None or require_answer:
            mapping = await lookup_slack_run_message_mapping(langgraph_client(), channel_id, run_id)
            if not mapping or mapping.get("run_id") != run_id:
                return
            if require_answer and mapping.get("should_ask_for_feedback") is not True:
                return
        if record is None:
            user_id = mapping.get("triggering_user_id")
            thread_ts = mapping.get("thread_ts")
            message_ts = mapping.get("message_ts")
            if not all(
                isinstance(value, str) and value for value in (user_id, thread_ts, message_ts)
            ):
                return
            record = ThreadFeedback(
                agent_thread_id=thread_id,
                run_id=run_id,
                channel_id=channel_id,
                thread_ts=str(thread_ts),
                message_ts=str(message_ts),
                user_id=str(user_id),
            )
        context = await get_slack_channel_context(channel_id, use_cache=False)
        if not slack_channel_allows_operations(context):
            return
        async with _locked_feedback(record) as current:
            record = current or record
            if record.prompted:
                return
            await store.put(run_id, record)
            posted = await post_slack_ephemeral_message(
                channel_id,
                record.user_id,
                "How did Open SWE do on this thread? Choose a rating or add comments.",
                thread_ts=record.thread_ts if record.thread_ts != "0" else None,
                blocks=rating_blocks(run_id, thread_id),
            )
            if posted:
                record.prompted = True
                await store.put(run_id, record)
    except Exception:
        # Feedback must not prevent the remaining completion hooks from running.
        logger.warning(
            "Could not post Slack feedback prompt", extra={"feedback_run_id": run_id}, exc_info=True
        )


async def post_slack_pr_feedback_prompt(
    thread_id: str, metadata: dict[str, Any], pr_url: str
) -> None:
    """Use the merged PR's original Slack run, never the latest conversation."""
    records = metadata.get("pull_requests")
    if not isinstance(records, list):
        return
    for record in records:
        if not isinstance(record, dict) or record.get("url") != pr_url:
            continue
        origin = record.get("slack_feedback")
        if not isinstance(origin, dict):
            return
        run_id, channel_id = origin.get("run_id"), origin.get("channel_id")
        if isinstance(run_id, str) and run_id and isinstance(channel_id, str) and channel_id:
            await post_slack_feedback_prompt(thread_id, run_id, channel_id)
        return


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _action(payload: dict[str, Any]) -> dict[str, Any]:
    actions = payload.get("actions")
    if isinstance(actions, list):
        for value in actions:
            action = _object(value)
            action_id = action.get("action_id")
            if isinstance(action_id, str) and (
                action_id.startswith(_RATE_PREFIX) or action_id == _COMMENT_ACTION
            ):
                return action
    return {}


def is_slack_feedback_payload(payload: dict[str, Any]) -> bool:
    return (payload.get("type") == "block_actions" and bool(_action(payload))) or (
        payload.get("type") == "view_submission"
        and _object(payload.get("view")).get("callback_id") == _COMMENT_ACTION
    )


async def _load_feedback(channel_id: str, run_id: str, user_id: str) -> ThreadFeedback | None:
    if not channel_id or not run_id or not user_id:
        return None
    record = await _store(channel_id).get(run_id)
    if record is None or record.user_id != user_id or record.channel_id != channel_id:
        return None
    context = await get_slack_channel_context(channel_id, use_cache=False)
    return record if slack_channel_allows_operations(context) else None


def comment_modal(record: ThreadFeedback) -> dict[str, Any]:
    element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": "comment",
        "multiline": True,
        "max_length": 3000,
        "placeholder": {"type": "plain_text", "text": "What worked well? What could be better?"},
    }
    if record.comment:
        element["initial_value"] = record.comment
    return {
        "type": "modal",
        "callback_id": _COMMENT_ACTION,
        "private_metadata": json.dumps({"channel_id": record.channel_id, "run_id": record.run_id}),
        "title": {"type": "plain_text", "text": "Open SWE feedback"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "plain_text",
                    "text": (
                        f"Your rating: {_RATINGS[record.rating - 1]}"
                        if record.rating is not None
                        else "Share your feedback on this thread. A rating is optional."
                    ),
                },
            },
            {
                "type": "input",
                "block_id": _COMMENT_BLOCK,
                "optional": record.rating is not None,
                "label": {"type": "plain_text", "text": "Comments"},
                "element": element,
            },
        ],
    }


async def _export_feedback(record: ThreadFeedback) -> None:
    try:
        # Serialize exports separately so a slow LangSmith call cannot block a modal save.
        async with _locked_feedback(record, purpose="feedback_export") as current:
            if current is not None:
                async with asyncio.timeout(8):
                    await _export_current_feedback(current)
    except Exception:
        logger.warning(
            "Could not export saved Slack feedback",
            extra={"feedback_run_id": record.run_id},
            exc_info=True,
        )


async def _export_current_feedback(record: ThreadFeedback) -> None:
    if record.rating is None and not record.comment:
        return
    synced = await create_langsmith_thread_feedback(
        record.agent_thread_id,
        f"slack_rating:{record.channel_id}:{record.user_id}:{record.run_id}",
        score=(record.rating - 1) / 4 if record.rating is not None else None,
        comment=record.comment or None,
        source_info={
            "source": "slack_thread_feedback",
            "channel_id": record.channel_id,
            "message_ts": record.message_ts,
            "user_id": record.user_id,
            "run_id": record.run_id,
        },
    )
    if not synced:
        logger.warning(
            "Slack feedback saved but LangSmith export failed",
            extra={"feedback_run_id": record.run_id},
        )


async def _acknowledge(record: ThreadFeedback, *, with_comment_button: bool) -> None:
    text = "Thanks — your feedback was saved."
    block: dict[str, Any] = {"type": "section", "text": {"type": "plain_text", "text": text}}
    if with_comment_button:
        block["accessory"] = {
            "type": "button",
            "text": {"type": "plain_text", "text": "Add comments"},
            "action_id": _COMMENT_ACTION,
            "value": record.run_id,
        }
    await post_slack_ephemeral_message(
        record.channel_id,
        record.user_id,
        text,
        thread_ts=record.thread_ts if record.thread_ts != "0" else None,
        blocks=[block],
    )


async def _process_rating(payload: dict[str, Any]) -> None:
    action = _action(payload)
    suffix = str(action.get("action_id", "")).removeprefix(_RATE_PREFIX)
    if suffix not in {"1", "2", "3", "4", "5"}:
        return
    channel_id = str(_object(payload.get("channel")).get("id") or "")
    user_id = str(_object(payload.get("user")).get("id") or "")
    run_id = str(action.get("value") or "")
    try:
        action_ts = Decimal(str(action.get("action_ts") or "0"))
    except InvalidOperation:
        return
    if not action_ts.is_finite() or action_ts <= 0:
        return
    try:
        record = await _load_feedback(channel_id, run_id, user_id)
        if record is None:
            return
        async with _locked_feedback(record) as current:
            if current is None or action_ts <= current.last_rating_ts:
                return
            record = current
            record.rating = int(suffix)
            record.last_rating_ts = action_ts
            await _store(channel_id).put(run_id, record)
    except Exception:
        logger.warning(
            "Could not save Slack rating", extra={"feedback_run_id": run_id}, exc_info=True
        )
        if channel_id and user_id:
            await post_slack_ephemeral_message(
                channel_id, user_id, "Your rating could not be saved. Please try again."
            )
        return
    await _acknowledge(record, with_comment_button=True)
    await _export_feedback(record)


def _comment_error(text: str) -> FeedbackResponse:
    return {"response_action": "errors", "errors": {_COMMENT_BLOCK: text}}


async def handle_slack_feedback_interaction(
    payload: dict[str, Any], background_tasks: BackgroundTasks
) -> FeedbackResponse:
    if payload.get("type") == "view_submission":
        try:
            async with asyncio.timeout(2.5):
                view = _object(payload.get("view"))
                metadata = _object(json.loads(str(view.get("private_metadata") or "{}")))
                record = await _load_feedback(
                    str(metadata.get("channel_id") or ""),
                    str(metadata.get("run_id") or ""),
                    str(_object(payload.get("user")).get("id") or ""),
                )
                if record is None:
                    return _comment_error(
                        "This feedback is unavailable. Please reopen the form from the prompt."
                    )
                values = _object(_object(view.get("state")).get("values"))
                comment = _object(_object(values.get(_COMMENT_BLOCK)).get("comment")).get("value")
                if comment is not None and (not isinstance(comment, str) or len(comment) > 3000):
                    return _comment_error("Comments must be at most 3,000 characters.")
                async with _locked_feedback(record) as current:
                    if current is None:
                        return _comment_error(
                            "This feedback is unavailable. Please reopen the form from the prompt."
                        )
                    record = current
                    record.comment = comment.strip() if isinstance(comment, str) else ""
                    if record.rating is None and not record.comment:
                        return _comment_error("Enter a comment or choose a rating in the prompt.")
                    await _store(record.channel_id).put(record.run_id, record)
        except Exception:
            logger.warning("Could not save Slack feedback comment", exc_info=True)
            return _comment_error("Your comment could not be saved. Please try again.")
        background_tasks.add_task(_acknowledge, record, with_comment_button=False)
        background_tasks.add_task(_export_feedback, record)
        return {}

    action = _action(payload)
    if action.get("action_id") != _COMMENT_ACTION:
        background_tasks.add_task(_process_rating, payload)
        return {}
    channel_id = str(_object(payload.get("channel")).get("id") or "")
    user_id = str(_object(payload.get("user")).get("id") or "")
    try:
        async with asyncio.timeout(2.5):
            record = await _load_feedback(channel_id, str(action.get("value") or ""), user_id)
            if record is None:
                return {}
            trigger_id = payload.get("trigger_id")
            if (
                isinstance(trigger_id, str)
                and trigger_id
                and await open_slack_modal(trigger_id, comment_modal(record))
            ):
                return {}
    except Exception:
        logger.warning("Could not open Slack feedback modal", exc_info=True)
    if channel_id and user_id:
        background_tasks.add_task(
            post_slack_ephemeral_message,
            channel_id,
            user_id,
            "The comment form could not be opened. Please click Add comments again.",
        )
    return {}
