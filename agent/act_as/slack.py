"""The act-as DM card and its button clicks."""

import json
import logging
from typing import Literal

from fastapi import BackgroundTasks

from agent.act_as.records import ActAsRequest, ThreadActAs
from agent.slack.blocks import Block, actions, block_payload, button, context, section
from agent.slack.client import (
    get_active_slack_thread,
    post_slack_ephemeral_message,
    post_slack_thread_reply,
    update_slack_message,
)
from agent.slack.payloads import SlackButtonValue, SlackInteraction
from agent.slack.responses import WebhookResponse, accepted, ignored
from agent.users import User
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

BUTTON_TYPE = "act_as"

CardAction = Literal["approve", "always_allow", "deny"]
_ACTIONS: dict[str, CardAction] = {
    "approve": "approve",
    "always_allow": "always_allow",
    "deny": "deny",
}
_LABELS: dict[CardAction, str] = {
    "approve": "Approved",
    "always_allow": "Always allowed",
    "deny": "Denied",
}


def card_blocks(message: str, request: ActAsRequest, thread_id: str) -> list[Block]:
    def _button(text: str, action: CardAction, style: Literal["primary", "danger"]):
        value = {
            "type": BUTTON_TYPE,
            "action": action,
            "fingerprint": request.fingerprint,
            "thread_id": thread_id,
        }
        return button(
            text,
            action_id=f"open_swe_option_select_act_as_{action}",
            value=json.dumps(value),
            style=style,
        )

    return [
        section(message),
        actions(
            _button("Approve", "approve", "primary"),
            _button("Always allow", "always_allow", "primary"),
            _button("Deny", "deny", "danger"),
        ),
    ]


async def handle_button(
    interaction: SlackInteraction, button: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    """Record the person's decision and tell the thread it came from."""
    channel_id = interaction.channel_id
    user_id = interaction.user.id
    if not channel_id or not user_id or not button.thread_id or not button.fingerprint:
        return ignored("Missing act-as context")
    action = _ACTIONS.get(button.action)
    if action is None:
        return ignored("Unknown act-as action")

    thread = await ThreadActAs.load(button.thread_id)
    request = thread.requests.get(button.fingerprint)
    if request is None:
        await post_slack_ephemeral_message(
            channel_id,
            user_id,
            "I couldn't find that request. Ask Open SWE to open the PR again.",
        )
        return ignored("act-as request not found")
    clicker = await User.login_for_slack(user_id)
    if not clicker or clicker.lower() != request.login.lower():
        await post_slack_ephemeral_message(
            channel_id, user_id, f"Only `{request.login}` can answer this request."
        )
        return ignored("act-as request answered by someone else")

    approved = action != "deny"
    await thread.decide(request, approved=approved, always_allow=action == "always_allow")
    background_tasks.add_task(_close_card, interaction, _LABELS[action])
    source = await get_active_slack_thread(langgraph_client(), button.thread_id)
    source_channel = (source or {}).get("channel_id")
    source_ts = (source or {}).get("thread_ts")
    if isinstance(source_channel, str) and isinstance(source_ts, str):
        verdict = "approved" if approved else "denied"
        await post_slack_thread_reply(
            source_channel,
            source_ts,
            f"`{request.login}` {verdict} Open SWE opening PRs as them in this thread.",
            agent_thread_id=button.thread_id,
        )
    return accepted("act-as request decided")


async def _close_card(interaction: SlackInteraction, label: str) -> None:
    text = interaction.message.text or label
    ok, error = await update_slack_message(
        interaction.channel_id,
        interaction.message_ts,
        text,
        blocks=block_payload([section(text), context(label)]),
    )
    if not ok:
        logger.warning(
            "Could not close the act-as card",
            extra={"channel_id": interaction.channel_id, "error": error},
        )
