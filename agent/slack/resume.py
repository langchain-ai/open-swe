"""Resuming an agent thread from a Block Kit click."""

import logging

from fastapi import HTTPException

from agent.dispatch import dispatch_agent_run
from agent.input_messages import SystemIdentity
from agent.prompts import render_prompt
from agent.slack.client import post_slack_ephemeral_message
from agent.slack.continuations import SlackContinuation
from agent.slack.payloads import SlackBlockAction, SlackInteraction
from agent.utils.thread_ops import get_thread_active_status, queue_message_for_thread
from agent.webhooks import common

logger = logging.getLogger(__name__)

_SENDER_ID = "system:slack-continuation"
_SENDER: SystemIdentity = {
    "id": _SENDER_ID,
    "display_name": "Slack interaction",
    "platform": "slack",
}

_NOT_YOURS = "That is not yours to answer — the thread behind it is private to someone else."
_FAILED = "Open SWE could not pick that up. Try again in a moment."
_SPENT = "That one has already been answered."


def _selected(value: object) -> str:
    """The label a person picked, out of whichever `selected_*` field holds it."""
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, dict) and isinstance(text.get("text"), str):
            return text["text"].strip()
        return str(value.get("value") or "").strip()
    if isinstance(value, list):
        return ", ".join(label for item in value if (label := _selected(item)))
    return str(value).strip() if value is not None else ""


def describe_action(action: SlackBlockAction) -> str:
    """What the person did, in the words their own screen used."""
    for field in (
        action.selected_option,
        action.selected_options,
        action.selected_user,
        action.selected_users,
        action.selected_conversation,
        action.selected_conversations,
        action.selected_channel,
        action.selected_channels,
        action.selected_date,
        action.selected_time,
        action.selected_date_time,
    ):
        if chosen := _selected(field):
            return f"chose `{chosen}`"
    if action.text and action.text.text.strip():
        return f"clicked *{action.text.text.strip()}*"
    if action.value and action.value.strip():
        return f"submitted `{action.value.strip()}`"
    return "interacted with it"


async def refuse_spent(channel_id: str, slack_user_id: str) -> None:
    """Tell one person their click landed on an element nothing is waiting on."""
    await post_slack_ephemeral_message(channel_id, slack_user_id, _SPENT)


async def refuse_not_yours(channel_id: str, slack_user_id: str) -> None:
    await post_slack_ephemeral_message(channel_id, slack_user_id, _NOT_YOURS)


async def clicker_may_resume(row: SlackContinuation, slack_user_id: str) -> bool:
    """Whether this person may prompt the thread the element belongs to.

    Asked before the click is claimed: a private thread's card can sit in a
    channel other people can see, and their click must not spend the answer its
    owner is still expected to give.
    """
    login = await common.login_for_slack_id(slack_user_id) or ""
    try:
        await common.authorize_github_thread(row.thread_id, login)
    except HTTPException:
        return False
    return True


async def resume(
    row: SlackContinuation, interaction: SlackInteraction, action: SlackBlockAction
) -> None:
    """Tell the thread behind `row` what was just clicked, reporting any failure."""
    try:
        await _resume(row, interaction, action)
    except Exception:
        logger.exception(
            "Failed to resume a thread from a Slack continuation",
            extra={"agent_thread_id": row.thread_id, "slack_continuation": str(row.id)},
        )
        try:
            await post_slack_ephemeral_message(row.channel_id, interaction.user.id, _FAILED)
        except Exception:  # noqa: BLE001
            logger.debug("Could not report the continuation failure to Slack", exc_info=True)


async def _resume(
    row: SlackContinuation, interaction: SlackInteraction, action: SlackBlockAction
) -> None:
    slack_user_id = interaction.user.id

    prompt = render_prompt(
        "runs/slack-continuation.md",
        who=interaction.user.username or interaction.user.name or f"<@{slack_user_id}>",
        action_id=row.action_id,
        label=row.label or row.action_id,
        element=row.element_type,
        what=describe_action(action),
    )
    if await get_thread_active_status(row.thread_id) and await queue_message_for_thread(
        row.thread_id, [{"type": "text", "text": prompt}]
    ):
        logger.info(
            "Queued a Slack continuation",
            extra={"agent_thread_id": row.thread_id, "slack_continuation": str(row.id)},
        )
        return

    await dispatch_agent_run(
        row.thread_id,
        prompt,
        dict(row.run_config),
        source="slack",
        context={"sender_id": _SENDER_ID, "surface": "slack", "kind": "system"},
        systems=[_SENDER],
    )
    logger.info(
        "Resumed a thread from a Slack continuation",
        extra={"agent_thread_id": row.thread_id, "slack_continuation": str(row.id)},
    )
