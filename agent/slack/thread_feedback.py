"""Private Slack ratings and comments on completed Open SWE threads."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

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
from agent.thread_feedback import complete_feedback_prompt
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.langsmith import create_langsmith_thread_feedback
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_FEEDBACK_ACTION = "open_swe_feedback"
_NOTE_ACTION = "open_swe_feedback_note"
_DISMISS_ACTION = "open_swe_feedback_dismiss"
_LEGACY_SELECT_PREFIX = "open_swe_feedback_select_"
_LEGACY_SUBMIT_ACTION = "open_swe_feedback_submit"
_LEGACY_RATING_ACTION = "open_swe_feedback_rating"
_COMMENT_BLOCK = "feedback_comment"
_LEGACY_RATING_BLOCK = "feedback_rating"


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
    choice: Literal["good", "bad"] | None = None
    comment: str = Field(default="", max_length=3000)
    completed: bool = False
    acknowledged: bool = False


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


def _comment_input() -> dict[str, Any]:
    return {
        "type": "input",
        "block_id": _COMMENT_BLOCK,
        "optional": True,
        "dispatch_action": False,
        "label": {"type": "plain_text", "text": "Comments"},
        "element": {
            "type": "plain_text_input",
            "action_id": "comment",
            "multiline": True,
            "max_length": 3000,
            "placeholder": {"type": "plain_text", "text": "What could be better?"},
        },
    }


def feedback_blocks(run_id: str, thread_id: str) -> list[dict[str, Any]]:
    url = dashboard_thread_url(thread_id)
    thread_link = f"<{url}|this thread>" if url else "this thread"
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"How did Open SWE do on {thread_link}?"},
        },
        {
            "type": "context_actions",
            "elements": [
                {
                    "type": "feedback_buttons",
                    "action_id": _FEEDBACK_ACTION,
                    "positive_button": {
                        "text": {"type": "plain_text", "text": "Good"},
                        "value": json.dumps({"run_id": run_id, "choice": "good"}),
                        "accessibility_label": "Rate this thread Good",
                    },
                    "negative_button": {
                        "text": {"type": "plain_text", "text": "Bad"},
                        "value": json.dumps({"run_id": run_id, "choice": "bad"}),
                        "accessibility_label": "Rate this thread Bad",
                    },
                }
            ],
        },
        {"type": "actions", "elements": [_dismiss_button(run_id)]},
    ]


async def post_slack_feedback_prompt(
    thread_id: str,
    run_id: str,
    channel_id: str,
    *,
    require_answer: bool = False,
    expected_event_id: str | None = None,
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
            if expected_event_id is not None:
                from agent.thread_feedback import feedback_event_is_ready

                if not await feedback_event_is_ready(thread_id, expected_event_id):
                    return
            posted = await post_slack_ephemeral_message(
                channel_id,
                record.user_id,
                "How did Open SWE do on this thread? Choose Good or Bad.",
                thread_ts=record.thread_ts if record.thread_ts != "0" else None,
                blocks=feedback_blocks(run_id, thread_id),
            )
            if posted:
                record.prompted = True
                await store.put(run_id, record)
    except Exception:
        # Feedback must not prevent the remaining completion hooks from running.
        logger.warning(
            "Could not post Slack feedback prompt", extra={"feedback_run_id": run_id}, exc_info=True
        )


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _action(payload: dict[str, Any]) -> dict[str, Any]:
    actions = payload.get("actions")
    if isinstance(actions, list):
        for value in actions:
            action = _object(value)
            action_id = action.get("action_id")
            if isinstance(action_id, str) and (
                action_id.startswith(_LEGACY_SELECT_PREFIX)
                or action_id
                in {
                    _FEEDBACK_ACTION,
                    _DISMISS_ACTION,
                    _LEGACY_SUBMIT_ACTION,
                    _LEGACY_RATING_ACTION,
                }
            ):
                return action
    return {}


def is_slack_feedback_payload(payload: dict[str, Any]) -> bool:
    return (payload.get("type") == "block_actions" and bool(_action(payload))) or (
        payload.get("type") == "view_submission"
        and _object(payload.get("view")).get("callback_id") == _NOTE_ACTION
    )


async def _load_feedback(channel_id: str, run_id: str, user_id: str) -> ThreadFeedback | None:
    if not channel_id or not run_id or not user_id:
        return None
    record = await _store(channel_id).get(run_id)
    if record is None or record.user_id != user_id or record.channel_id != channel_id:
        return None
    context = await get_slack_channel_context(channel_id, use_cache=False)
    return record if slack_channel_allows_operations(context) else None


def comment_modal(record: ThreadFeedback, response_url: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": _NOTE_ACTION,
        "private_metadata": json.dumps(
            {
                "channel_id": record.channel_id,
                "run_id": record.run_id,
                "response_url": response_url,
            }
        ),
        "title": {"type": "plain_text", "text": "Open SWE feedback"},
        "submit": {"type": "plain_text", "text": "Submit comment"},
        "close": {"type": "plain_text", "text": "Skip"},
        "blocks": [_comment_input()],
    }


async def _export_feedback(record: ThreadFeedback) -> None:
    try:
        # Serialize exports separately so a slow LangSmith call cannot block a modal save.
        async with _locked_feedback(record, purpose="feedback_export") as current:
            if current is None or not current.completed:
                return
            async with asyncio.timeout(8):
                synced = await create_langsmith_thread_feedback(
                    current.agent_thread_id,
                    "rating",
                    score=(current.rating - 1) / 4 if current.rating is not None else None,
                    comment=current.comment or None,
                    source_info={
                        "source": "slack_thread_feedback",
                        "channel_id": current.channel_id,
                        "message_ts": current.message_ts,
                        "user_id": current.user_id,
                        "run_id": current.run_id,
                    },
                )
            if not synced:
                logger.warning(
                    "Slack feedback saved but LangSmith export failed",
                    extra={"feedback_run_id": current.run_id},
                )
    except Exception:
        logger.warning(
            "Could not export saved Slack feedback",
            extra={"feedback_run_id": record.run_id},
            exc_info=True,
        )


async def _acknowledge(record: ThreadFeedback, *, response_url: str) -> None:
    try:
        async with _locked_feedback(record, purpose="feedback_response") as current:
            if current is None or current.dismissed or not current.completed or not response_url:
                return
            async with asyncio.timeout(8):
                if not current.acknowledged:
                    # Ephemeral response_url updates can also appear at the channel root.
                    posted = await post_slack_ephemeral_message(
                        current.channel_id,
                        current.user_id,
                        "✅ Feedback completed. Thanks!",
                        thread_ts=current.thread_ts if current.thread_ts != "0" else None,
                    )
                    if not posted:
                        return
                    async with _locked_feedback(current) as latest:
                        if latest is None:
                            return
                        latest.acknowledged = True
                        await _store(latest.channel_id).put(latest.run_id, latest)
                await respond_to_slack_interaction(response_url, {"delete_original": True})
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
                await complete_feedback_prompt(record.agent_thread_id, "dismissed")
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


async def _save_comment(record: ThreadFeedback, values: dict[str, Any]) -> ThreadFeedback:
    async with _locked_feedback(record) as current:
        if current is None:
            raise _FeedbackInputError("This feedback is unavailable. Please reopen the prompt.")
        if current.dismissed or not current.completed or current.choice != "bad":
            raise _FeedbackInputError("Comments require a submitted Bad rating.")
        comment = _object(_object(values.get(_COMMENT_BLOCK)).get("comment")).get("value")
        if comment is not None and (not isinstance(comment, str) or len(comment) > 3000):
            raise _FeedbackInputError("Comments must be at most 3,000 characters.")
        comment = comment.strip() if isinstance(comment, str) else ""
        if comment and not current.comment:
            current.comment = comment
            await _store(current.channel_id).put(current.run_id, current)
        return current


def _comment_error(text: str) -> FeedbackResponse:
    return {"response_action": "errors", "errors": {_COMMENT_BLOCK: text}}


def _legacy_submission(payload: dict[str, Any]) -> tuple[str, str] | None:
    action = _action(payload)
    action_id = str(action.get("action_id") or "")
    if action_id.startswith(_LEGACY_SELECT_PREFIX):
        choice = action_id.removeprefix(_LEGACY_SELECT_PREFIX)
        return (str(action.get("value") or ""), choice) if choice in {"good", "bad"} else None
    if action_id != _LEGACY_SUBMIT_ACTION:
        return None
    run_id = str(action.get("value") or "")
    choice = ""
    if run_id.startswith("{"):
        selection = _object(json.loads(run_id))
        run_id = str(selection.get("run_id") or "")
        choice = str(selection.get("choice") or "")
        rating = selection.get("rating")
        if not choice and rating in {1, 5}:
            choice = "good" if rating == 5 else "bad"
    if not choice:
        selected = _object(
            _object(
                _object(_object(payload.get("state")).get("values")).get(_LEGACY_RATING_BLOCK)
            ).get(_LEGACY_RATING_ACTION)
        ).get("selected_option")
        value = str(_object(selected).get("value") or "")
        choice = value if value in {"good", "bad"} else {"1": "bad", "5": "good"}.get(value, "")
    return (run_id, choice) if run_id and choice else None


def _native_rating_payload(payload: dict[str, Any], run_id: str, choice: str) -> dict[str, Any]:
    return {
        **payload,
        "actions": [
            {
                **_action(payload),
                "action_id": _FEEDBACK_ACTION,
                "value": json.dumps({"run_id": run_id, "choice": choice}),
            }
        ],
    }


async def _record_rating(payload: dict[str, Any], background_tasks: BackgroundTasks) -> None:
    try:
        async with asyncio.timeout(2.5):
            selection = _object(json.loads(str(_action(payload).get("value") or "{}")))
            choice = selection.get("choice")
            if choice not in {"good", "bad"}:
                return
            record = await _load_feedback(
                str(_object(payload.get("channel")).get("id") or ""),
                str(selection.get("run_id") or ""),
                str(_object(payload.get("user")).get("id") or ""),
            )
            if record is None:
                return
            async with _locked_feedback(record) as current:
                if current is None or current.dismissed:
                    return
                newly_saved = not current.completed
                if newly_saved:
                    current.choice = choice
                    current.rating = 5 if choice == "good" else 1
                    current.completed = True
                    await _store(current.channel_id).put(current.run_id, current)
                record = current
            response_url = str(payload.get("response_url") or "")
            background_tasks.add_task(complete_feedback_prompt, record.agent_thread_id, "completed")
            background_tasks.add_task(_acknowledge, record, response_url=response_url)
            background_tasks.add_task(_export_feedback, record)
            trigger_id = payload.get("trigger_id")
            if newly_saved and choice == "bad" and isinstance(trigger_id, str) and trigger_id:
                await open_slack_modal(trigger_id, comment_modal(record, response_url))
    except Exception:
        logger.warning("Could not process Slack feedback rating", exc_info=True)


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
                record = await _save_comment(record, values)
        except _FeedbackInputError as exc:
            return _comment_error(str(exc))
        except Exception:
            logger.warning("Could not save Slack feedback submission")
            return _comment_error("Your feedback could not be saved. Please try again.")
        background_tasks.add_task(complete_feedback_prompt, record.agent_thread_id, "completed")
        background_tasks.add_task(
            _acknowledge, record, response_url=str(metadata.get("response_url") or "")
        )
        background_tasks.add_task(_export_feedback, record)
        return {}

    action = _action(payload)
    action_id = action.get("action_id")
    if action_id == _FEEDBACK_ACTION:
        await _record_rating(payload, background_tasks)
        return {}
    if action_id == _DISMISS_ACTION:
        background_tasks.add_task(_dismiss_feedback, payload)
        return {}
    if action_id == _LEGACY_RATING_ACTION:
        return {}
    try:
        legacy = _legacy_submission(payload)
    except Exception:
        logger.warning("Could not parse legacy Slack feedback rating", exc_info=True)
        return {}
    if legacy is not None:
        await _record_rating(_native_rating_payload(payload, *legacy), background_tasks)
    return {}
