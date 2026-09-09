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
    respond_to_slack_interaction,
    slack_channel_allows_operations,
    slack_thread_mutation_lock,
)
from agent.slack.responses import FeedbackResponse
from agent.source_context import SourceContext
from agent.store import TypedStore
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.langsmith import create_langsmith_thread_feedback
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_RATE_PREFIX = "open_swe_feedback_rate_"
_SELECT_PREFIX = "open_swe_feedback_select_"
_COMMENT_ACTION = "open_swe_feedback_comment"
_DISMISS_ACTION = "open_swe_feedback_dismiss"
_SUBMIT_ACTION = "open_swe_feedback_submit"
_RATING_ACTION = "open_swe_feedback_rating"
_RATING_BLOCK = "feedback_rating"
_COMMENT_BLOCK = "feedback_comment"
_RATINGS = ("😡 Very Bad", "💩 Bad", "😐 Okay", "🙂 Good", "😍 Great")


class ThreadFeedback(BaseModel):
    agent_thread_id: str
    run_id: str
    channel_id: str
    thread_ts: str
    message_ts: str
    user_id: str
    prompted: bool = False
    dismissed: bool = False
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str = Field(default="", max_length=3000)
    completed: bool = False
    submitted_at: Decimal | None = None
    draft_rating: int | None = Field(default=None, ge=1, le=5)
    last_selection_ts: Decimal = Decimal(0)


def _store(channel_id: str) -> TypedStore[ThreadFeedback]:
    return TypedStore(("slack_thread_feedback", channel_id), ThreadFeedback)


@asynccontextmanager
async def _locked_feedback(
    record: ThreadFeedback, *, purpose: str = "feedback"
) -> AsyncIterator[ThreadFeedback | None]:
    lock_purpose = f"{purpose}:{record.user_id}"
    if record.thread_ts == "0":
        lock_purpose += f":{record.agent_thread_id}"
    async with slack_thread_mutation_lock(
        langgraph_client(), record.channel_id, record.thread_ts, purpose=lock_purpose
    ):
        yield await _store(record.channel_id).get(record.run_id)


def _dismiss_button(run_id: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": "Dismiss"},
        "action_id": _DISMISS_ACTION,
        "value": run_id,
    }


def _comment_input(comment: str = "") -> dict[str, Any]:
    comment_element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": "comment",
        "multiline": True,
        "max_length": 3000,
        "placeholder": {"type": "plain_text", "text": "What worked well? What could be better?"},
    }
    if comment:
        comment_element["initial_value"] = comment
    return {
        "type": "input",
        "block_id": _COMMENT_BLOCK,
        "optional": True,
        "dispatch_action": False,
        "label": {"type": "plain_text", "text": "Comments"},
        "element": comment_element,
    }


def _feedback_inputs(rating: int | None = None, comment: str = "") -> list[dict[str, Any]]:
    options = [
        {"text": {"type": "plain_text", "text": label, "emoji": True}, "value": str(value)}
        for value, label in enumerate(_RATINGS, start=1)
    ]
    rating_element: dict[str, Any] = {
        "type": "radio_buttons",
        "action_id": _RATING_ACTION,
        "options": options,
    }
    if rating is not None:
        rating_element["initial_option"] = options[rating - 1]
    return [
        {
            "type": "input",
            "block_id": _RATING_BLOCK,
            "optional": True,
            "dispatch_action": False,
            "label": {"type": "plain_text", "text": "Rating"},
            "element": rating_element,
        },
        _comment_input(comment),
    ]


def rating_blocks(
    run_id: str,
    thread_id: str,
    *,
    rating: int | None = None,
    comment: str = "",
    error: str = "",
    selection_ts: Decimal = Decimal(0),
) -> list[dict[str, Any]]:
    url = dashboard_thread_url(thread_id)
    thread_link = f"<{url}|this thread>" if url else "this thread"
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"How did Open SWE do on {thread_link}?",
            },
        }
    ]
    if error:
        blocks.append({"type": "section", "text": {"type": "plain_text", "text": error}})
    return [
        *blocks,
        {
            "type": "actions",
            "block_id": _RATING_BLOCK,
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": label, "emoji": True},
                    "action_id": f"{_SELECT_PREFIX}{value}",
                    "value": run_id,
                    **({"style": "primary"} if value == rating else {}),
                }
                for value, label in enumerate(_RATINGS, start=1)
            ],
        },
        _comment_input(comment),
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Submit feedback"},
                    "action_id": _SUBMIT_ACTION,
                    "value": json.dumps(
                        {"run_id": run_id, "rating": rating, "selection_ts": str(selection_ts)}
                    )
                    if rating is not None
                    else run_id,
                    "style": "primary",
                },
                _dismiss_button(run_id),
            ],
        },
    ]


async def post_slack_feedback_prompt(
    thread_id: str, run_id: str, channel_id: str, *, require_answer: bool = False
) -> None:
    """Prompt the thread initiator once, using the qualifying run's response mapping."""
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
        thread_ts = record.thread_ts if record else mapping.get("thread_ts")
        if not isinstance(thread_ts, str) or not thread_ts:
            return
        thread = await langgraph_client().threads.get(thread_id)
        origin = SourceContext.from_metadata(thread.get("metadata")).slack_thread
        if (
            origin is None
            or not origin.is_at(channel_id, thread_ts)
            or not origin.triggering_user_id
        ):
            return
        if record is not None and record.user_id != origin.triggering_user_id:
            return
        if record is None:
            message_ts = mapping.get("message_ts")
            if not isinstance(message_ts, str) or not message_ts:
                return
            record = ThreadFeedback(
                agent_thread_id=thread_id,
                run_id=run_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                message_ts=message_ts,
                user_id=origin.triggering_user_id,
            )
        context = await get_slack_channel_context(channel_id, use_cache=False)
        if not slack_channel_allows_operations(context):
            return
        async with _locked_feedback(record) as current:
            record = current or record
            if record.prompted:
                return
            prompt_filter: dict[str, Any] = {
                "thread_ts": record.thread_ts,
                "user_id": record.user_id,
                "prompted": True,
            }
            # Code channel sessions share the "0" timestamp across agent threads.
            if record.thread_ts == "0":
                prompt_filter["agent_thread_id"] = record.agent_thread_id
            if await store.search(filter=prompt_filter, limit=1):
                return
            await store.put(run_id, record)
            posted = await post_slack_ephemeral_message(
                channel_id,
                record.user_id,
                "How did Open SWE do on this thread? Add a rating or comment, then submit feedback.",
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
                action_id.startswith(_RATE_PREFIX)
                or action_id.startswith(_SELECT_PREFIX)
                or action_id in {_COMMENT_ACTION, _DISMISS_ACTION, _SUBMIT_ACTION, _RATING_ACTION}
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


def comment_modal(record: ThreadFeedback, response_url: str = "") -> dict[str, Any]:
    """Let already-posted rating/comment buttons open the combined feedback form."""
    return {
        "type": "modal",
        "callback_id": _COMMENT_ACTION,
        "private_metadata": json.dumps(
            {
                "channel_id": record.channel_id,
                "run_id": record.run_id,
                "response_url": response_url,
            }
        ),
        "title": {"type": "plain_text", "text": "Open SWE feedback"},
        "submit": {"type": "plain_text", "text": "Submit feedback"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": _feedback_inputs(record.rating, record.comment),
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
    if not record.completed:
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


async def _acknowledge(record: ThreadFeedback, *, response_url: str) -> None:
    try:
        async with _locked_feedback(record, purpose="feedback_response") as current:
            if current is None or current.dismissed or not current.completed or not response_url:
                return
            text = "✅ Feedback completed. Thanks!"
            async with asyncio.timeout(8):
                await respond_to_slack_interaction(
                    response_url,
                    {
                        "replace_original": True,
                        "response_type": "ephemeral",
                        "text": text,
                        "blocks": [
                            {"type": "section", "text": {"type": "plain_text", "text": text}}
                        ],
                    },
                )
    except Exception:
        logger.warning("Could not acknowledge saved Slack feedback")


async def _dismiss_feedback(payload: dict[str, Any]) -> None:
    channel_id = str(_object(payload.get("channel")).get("id") or "")
    user_id = str(_object(payload.get("user")).get("id") or "")
    try:
        record = await _load_feedback(channel_id, str(_action(payload).get("value") or ""), user_id)
        if record is None:
            return
        async with _locked_feedback(record, purpose="feedback_response") as current:
            if current is None:
                return
            async with _locked_feedback(current) as latest:
                if latest is None:
                    return
                was_dismissed = latest.dismissed
                if not was_dismissed:
                    latest.dismissed = True
                    await _store(channel_id).put(latest.run_id, latest)
            if await respond_to_slack_interaction(
                str(payload.get("response_url") or ""), {"delete_original": True}
            ):
                return
            if not was_dismissed:
                async with _locked_feedback(current) as latest:
                    if latest is not None:
                        latest.dismissed = False
                        await _store(channel_id).put(latest.run_id, latest)
    except Exception:
        logger.warning("Could not dismiss Slack feedback prompt")
        return
    logger.warning("Could not dismiss Slack feedback prompt")


class _FeedbackInputError(ValueError):
    pass


def _form_values(
    values: dict[str, Any], default_rating: int | None = None
) -> tuple[int | None, str]:
    selected = _object(_object(values.get(_RATING_BLOCK)).get(_RATING_ACTION)).get(
        "selected_option"
    )
    value = _object(selected).get("value")
    if selected is not None and (
        not isinstance(value, str) or value not in {"1", "2", "3", "4", "5"}
    ):
        raise _FeedbackInputError("Choose a rating from 1 to 5.")
    rating = int(value) if value is not None else default_rating
    comment = _object(_object(values.get(_COMMENT_BLOCK)).get("comment")).get("value")
    if comment is not None and (not isinstance(comment, str) or len(comment) > 3000):
        raise _FeedbackInputError("Comments must be at most 3,000 characters.")
    comment = comment.strip() if isinstance(comment, str) else ""
    return rating, comment


async def _save_submission(
    record: ThreadFeedback,
    values: dict[str, Any],
    *,
    legacy_modal: bool = False,
    submitted_at: Decimal | None = None,
    selection_ts: Decimal = Decimal(0),
) -> ThreadFeedback:
    async with _locked_feedback(record) as current:
        if current is None:
            raise _FeedbackInputError("This feedback is unavailable. Please reopen the prompt.")
        if current.completed or current.dismissed:
            return current
        default_rating = current.rating if legacy_modal and _RATING_BLOCK not in values else None
        rating, comment = _form_values(values, default_rating)
        completed = rating is not None or bool(comment)
        if not completed and submitted_at is None:
            raise _FeedbackInputError("Choose a rating or enter a comment before submitting.")
        current.rating = rating
        current.comment = comment
        current.completed = completed
        current.submitted_at = submitted_at
        current.last_selection_ts = max(current.last_selection_ts, selection_ts)
        await _store(current.channel_id).put(current.run_id, current)
        if not completed:
            raise _FeedbackInputError("Choose a rating or enter a comment before submitting.")
        return current


async def _show_submission_error(
    record: ThreadFeedback,
    values: dict[str, Any],
    response_url: str,
    error: str,
    selection_ts: Decimal,
) -> None:
    selected = _object(
        _object(_object(values.get(_RATING_BLOCK)).get(_RATING_ACTION)).get("selected_option")
    ).get("value")
    rating = (
        int(selected)
        if isinstance(selected, str) and selected in {"1", "2", "3", "4", "5"}
        else None
    )
    comment = _object(_object(values.get(_COMMENT_BLOCK)).get("comment")).get("value")
    comment = comment[:3000] if isinstance(comment, str) else ""
    current = await _store(record.channel_id).get(record.run_id)
    if current is None or current.completed or current.dismissed or not response_url:
        return
    await respond_to_slack_interaction(
        response_url,
        {
            "replace_original": True,
            "response_type": "ephemeral",
            "text": error,
            "blocks": rating_blocks(
                record.run_id,
                record.agent_thread_id,
                rating=rating,
                comment=comment,
                error=error,
                selection_ts=selection_ts,
            ),
        },
    )


async def _process_submission(payload: dict[str, Any]) -> None:
    channel_id = str(_object(payload.get("channel")).get("id") or "")
    user_id = str(_object(payload.get("user")).get("id") or "")
    response_url = str(payload.get("response_url") or "")
    values = _object(_object(payload.get("state")).get("values"))
    try:
        action_value = str(_action(payload).get("value") or "")
        selection_ts = Decimal(0)
        if action_value.startswith("{"):
            selection = _object(json.loads(action_value))
            action_value = str(selection.get("run_id") or "")
            selection_ts = Decimal(str(selection.get("selection_ts") or "0"))
            if not selection_ts.is_finite() or selection_ts < 0:
                return
            values = {
                **values,
                _RATING_BLOCK: {
                    _RATING_ACTION: {"selected_option": {"value": str(selection.get("rating"))}}
                },
            }
        record = await _load_feedback(channel_id, action_value, user_id)
        if record is None:
            return
        async with _locked_feedback(record, purpose="feedback_response") as current:
            if current is None:
                return
            action_ts = _action(payload).get("action_ts")
            submitted_at = None
            if action_ts is not None:
                submitted_at = Decimal(str(action_ts))
                if (
                    not submitted_at.is_finite()
                    or submitted_at <= 0
                    or submitted_at < current.last_selection_ts
                    or (current.submitted_at is not None and submitted_at < current.submitted_at)
                ):
                    return
            # A Submit click can reach us before the preceding emoji update reaches Slack.
            if current.draft_rating is not None and current.last_selection_ts > selection_ts:
                selection_ts = current.last_selection_ts
                values = {
                    **values,
                    _RATING_BLOCK: {
                        _RATING_ACTION: {"selected_option": {"value": str(current.draft_rating)}}
                    },
                }
            try:
                record = await _save_submission(
                    current, values, submitted_at=submitted_at, selection_ts=selection_ts
                )
            except Exception as exc:
                error = "Your feedback could not be saved. Please submit again."
                if isinstance(exc, _FeedbackInputError):
                    error = str(exc)
                else:
                    logger.warning("Could not save Slack feedback submission")
                await _show_submission_error(current, values, response_url, error, selection_ts)
                return
        await _acknowledge(record, response_url=response_url)
        await _export_feedback(record)
    except Exception:
        logger.warning("Could not process Slack feedback submission")


def _comment_error(text: str) -> FeedbackResponse:
    return {"response_action": "errors", "errors": {_COMMENT_BLOCK: text}}


async def _select_feedback_rating(payload: dict[str, Any]) -> None:
    action = _action(payload)
    suffix = str(action.get("action_id", "")).removeprefix(_SELECT_PREFIX)
    if suffix not in {"1", "2", "3", "4", "5"}:
        return
    try:
        timestamp = Decimal(str(action.get("action_ts") or "0"))
    except InvalidOperation:
        return
    if not timestamp.is_finite() or timestamp <= 0:
        return
    channel_id = str(_object(payload.get("channel")).get("id") or "")
    user_id = str(_object(payload.get("user")).get("id") or "")
    response_url = str(payload.get("response_url") or "")
    try:
        record = await _load_feedback(channel_id, str(action.get("value") or ""), user_id)
        if record is None:
            return
        async with _locked_feedback(record, purpose="feedback_response") as current:
            if current is None or current.dismissed or timestamp <= current.last_selection_ts:
                return
            if current.submitted_at is not None and timestamp < current.submitted_at:
                # Slack can deliver a pre-Submit click after the submission has been saved.
                async with _locked_feedback(current) as latest:
                    if (
                        latest is None
                        or latest.dismissed
                        or latest.submitted_at is None
                        or not latest.last_selection_ts < timestamp < latest.submitted_at
                    ):
                        return
                    latest.rating = latest.draft_rating = int(suffix)
                    latest.last_selection_ts = timestamp
                    latest.completed = True
                    record = await _store(channel_id).put(latest.run_id, latest)
            else:
                if current.completed or not response_url:
                    return
                values = _object(_object(payload.get("state")).get("values"))
                comment = _object(_object(values.get(_COMMENT_BLOCK)).get("comment")).get("value")
                comment = comment[:3000] if isinstance(comment, str) else ""
                posted = await respond_to_slack_interaction(
                    response_url,
                    {
                        "replace_original": True,
                        "response_type": "ephemeral",
                        "text": "Add a rating or comment, then submit feedback.",
                        "blocks": rating_blocks(
                            current.run_id,
                            current.agent_thread_id,
                            rating=int(suffix),
                            comment=comment,
                            selection_ts=timestamp,
                        ),
                    },
                )
                if posted:
                    async with _locked_feedback(current) as latest:
                        if latest is not None and not latest.completed and not latest.dismissed:
                            latest.draft_rating = int(suffix)
                            latest.last_selection_ts = timestamp
                            latest.submitted_at = None
                            await _store(channel_id).put(latest.run_id, latest)
                return
        await _acknowledge(record, response_url=response_url)
        await _export_feedback(record)
    except Exception:
        logger.warning("Could not update Slack feedback rating selection")


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
                record = await _save_submission(record, values, legacy_modal=True)
        except _FeedbackInputError as exc:
            return _comment_error(str(exc))
        except Exception:
            logger.warning("Could not save Slack feedback submission")
            return _comment_error("Your feedback could not be saved. Please try again.")
        background_tasks.add_task(
            _acknowledge, record, response_url=str(metadata.get("response_url") or "")
        )
        background_tasks.add_task(_export_feedback, record)
        return {}

    action = _action(payload)
    action_id = action.get("action_id")
    if action_id == _DISMISS_ACTION:
        background_tasks.add_task(_dismiss_feedback, payload)
        return {}
    if action_id == _SUBMIT_ACTION:
        background_tasks.add_task(_process_submission, payload)
        return {}
    if action_id == _RATING_ACTION:
        return {}
    if isinstance(action_id, str) and action_id.startswith(_SELECT_PREFIX):
        background_tasks.add_task(_select_feedback_rating, payload)
        return {}
    selected_rating = None
    if isinstance(action_id, str) and action_id.startswith(_RATE_PREFIX):
        suffix = action_id.removeprefix(_RATE_PREFIX)
        if suffix not in {"1", "2", "3", "4", "5"}:
            return {}
        selected_rating = int(suffix)
    channel_id = str(_object(payload.get("channel")).get("id") or "")
    user_id = str(_object(payload.get("user")).get("id") or "")
    try:
        async with asyncio.timeout(2.5):
            record = await _load_feedback(channel_id, str(action.get("value") or ""), user_id)
            if record is None or record.dismissed:
                return {}
            if record.completed:
                background_tasks.add_task(
                    _acknowledge, record, response_url=str(payload.get("response_url") or "")
                )
                return {}
            if selected_rating is not None:
                record = record.model_copy(update={"rating": selected_rating})
            trigger_id = payload.get("trigger_id")
            if (
                isinstance(trigger_id, str)
                and trigger_id
                and await open_slack_modal(
                    trigger_id, comment_modal(record, str(payload.get("response_url") or ""))
                )
            ):
                return {}
    except Exception:
        logger.warning("Could not open Slack feedback form")
    return {}
