"""Slack plan revision modals and their authorized follow-up runs."""

import logging
from collections.abc import Mapping

from fastapi import BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, ValidationError

from agent.slack.blocks import modal, text_input, view_payload
from agent.slack.channels import SlackChannel
from agent.slack.client import (
    get_slack_user_info,
    lookup_slack_thread_id,
    open_slack_modal,
    post_slack_ephemeral_message,
)
from agent.slack.payloads import SlackButtonValue, SlackInteraction, SlackViewSubmission
from agent.slack.responses import FeedbackResponse, WebhookResponse, accepted
from agent.source_context import SlackThreadRef
from agent.threads.plan_api import request_plan_changes_from_slack
from agent.users import User
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)
CALLBACK_ID = "open_swe_plan_feedback"
FEEDBACK_BLOCK = "plan_feedback"
FEEDBACK_ACTION = "feedback"
FEEDBACK_ACCEPTED = (
    "Your feedback was saved and a plan revision was requested. Implementation is still paused."
)
FEEDBACK_FAILED = "Your plan revision could not be started. Please retry from the latest plan review. Any feedback already saved remains on the plan."
FEEDBACK_OPEN_FAILED = "The feedback form could not open. Click Request changes again, or reply in the thread with your feedback."
FEEDBACK_STALE = "This plan review is no longer available or you do not have access. Open the latest plan review and try again; for an older card, reply in the thread with your requested changes."
FEEDBACK_REQUIRED = "Enter your requested changes (1–3000 characters)."


class PlanFeedbackContext(BaseModel):
    thread_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    thread_ts: str = Field(min_length=1)
    message_ts: str = Field(min_length=1)
    reply_thread_ts: str = ""
    user_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=64, max_length=64)


async def _notify(context: PlanFeedbackContext, message: str) -> None:
    await _notify_at(
        context.channel_id, context.user_id, context.reply_thread_ts or context.thread_ts, message
    )


async def _notify_at(channel_id: str, user_id: str, thread_ts: str, message: str) -> None:
    try:
        posted = await post_slack_ephemeral_message(
            channel_id,
            user_id,
            message,
            thread_ts=None if thread_ts == "0" else thread_ts,
        )
        if not posted:
            logger.error("Slack plan feedback notification was not delivered")
    except Exception:
        logger.exception("Could not notify Slack plan reviewer")


def is_plan_feedback_submission(payload: Mapping[str, object]) -> bool:
    view = payload.get("view")
    return payload.get("type") == "view_submission" and (
        isinstance(view, Mapping) and view.get("callback_id") == CALLBACK_ID
    )


async def open_feedback(
    interaction: SlackInteraction, button: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    try:
        context = PlanFeedbackContext(
            thread_id=button.thread_id,
            channel_id=interaction.channel_id,
            thread_ts=button.thread_ts,
            message_ts=interaction.message_ts,
            reply_thread_ts=(interaction.message.thread_ts or interaction.container.thread_ts)
            if button.thread_ts == "0"
            else "",
            user_id=interaction.user.id,
            fingerprint=button.fingerprint,
        )
    except ValidationError:
        logger.warning("Slack plan button is missing revision context")
        background_tasks.add_task(
            _notify_at,
            interaction.channel_id,
            interaction.user.id,
            interaction.thread_ts,
            FEEDBACK_STALE,
        )
        return accepted("Plan review expired")
    opened = False
    try:
        if interaction.trigger_id:
            opened = await open_slack_modal(
                interaction.trigger_id,
                view_payload(
                    modal(
                        callback_id=CALLBACK_ID,
                        title="Request plan changes",
                        submit="Request changes",
                        private_metadata=context.model_dump_json(),
                        blocks=[
                            text_input(
                                block_id=FEEDBACK_BLOCK,
                                action_id=FEEDBACK_ACTION,
                                label="What should change in the plan?",
                                multiline=True,
                                max_length=3000,
                            )
                        ],
                    )
                ),
            )
    except Exception:
        logger.exception("Could not open Slack plan feedback modal")
    if not opened:
        background_tasks.add_task(_notify, context, FEEDBACK_OPEN_FAILED)
    return accepted("Plan feedback requested" if opened else "Plan feedback modal unavailable")


async def handle_submission(
    payload: Mapping[str, object], background_tasks: BackgroundTasks
) -> FeedbackResponse:
    submission = SlackViewSubmission.parse(payload)
    try:
        context = PlanFeedbackContext.model_validate(submission.metadata if submission else {})
    except ValidationError:
        logger.warning("Invalid Slack plan feedback context")
        return _error(FEEDBACK_STALE)
    if submission is None or submission.user.id != context.user_id:
        return _error(FEEDBACK_STALE)
    feedback = submission.submitted(FEEDBACK_BLOCK, FEEDBACK_ACTION).strip()
    if not feedback or len(feedback) > 3000:
        return _error(FEEDBACK_REQUIRED)
    background_tasks.add_task(_revise, context, submission, feedback)
    return {}


def _error(message: str) -> FeedbackResponse:
    return {"response_action": "errors", "errors": {FEEDBACK_BLOCK: message}}


async def _revise(
    context: PlanFeedbackContext, submission: SlackViewSubmission, feedback: str
) -> None:
    try:
        channel = await SlackChannel.context_for(context.channel_id, use_cache=False)
        if not channel.allows_operations:
            await _notify(context, FEEDBACK_STALE)
            return
        mapped = await lookup_slack_thread_id(
            langgraph_client(), context.channel_id, context.thread_ts
        )
        if mapped != context.thread_id:
            await _notify(context, FEEDBACK_STALE)
            return
        user = await get_slack_user_info(context.user_id) or {}
        profile = user.get("profile")
        profile = profile if isinstance(profile, dict) else {}
        email = profile.get("email")
        email = email if isinstance(email, str) else ""
        timezone = user.get("tz")
        login = await User.login_for_slack(context.user_id) or await User.login_for_email(email)
        await request_plan_changes_from_slack(
            context.thread_id,
            feedback=feedback,
            fingerprint=context.fingerprint,
            github_login=login,
            slack_thread=SlackThreadRef(
                channel_id=context.channel_id,
                thread_ts=context.thread_ts,
                reply_thread_ts=context.reply_thread_ts,
                channel_context=channel,
                team_id=submission.team.id,
                triggering_user_id=context.user_id,
                triggering_user_name=submission.user.name
                or submission.user.username
                or context.user_id,
                triggering_user_email=email,
                triggering_user_timezone=timezone if isinstance(timezone, str) else "",
            ),
        )
    except HTTPException as exc:
        logger.warning("Slack plan revision rejected", exc_info=True)
        await _notify(
            context,
            FEEDBACK_STALE if exc.status_code in {403, 404, 409} else FEEDBACK_FAILED,
        )
        return
    except Exception:
        logger.exception("Slack plan revision failed", extra={"agent_thread_id": context.thread_id})
        await _notify(context, FEEDBACK_FAILED)
        return
    await _notify(context, FEEDBACK_ACCEPTED)
