"""Privately offer whoever links a pull request in Slack to get it reviewed.

The offer is an ephemeral message only the poster sees. Clicking it hands the
Slack thread's agent thread, creating one when there is none, a request to put
the pull request up for review; the agent asks for the review when it is ready
and replies in the thread only when it is not.
"""

import json
import logging

from fastapi import BackgroundTasks

from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequestPayload
from openswe.human_review.requests import HumanReviewRequest
from openswe.prompts import prompt
from openswe.slack import webhook as service
from openswe.slack.blocks import ButtonElement, actions, block_payload, button, section
from openswe.slack.client import (
    GitHubPrRef,
    parse_github_pr_url,
    post_slack_ephemeral_message,
    respond_to_slack_interaction,
)
from openswe.slack.payloads import SlackButtonValue, SlackInteraction
from openswe.slack.request import SlackRequest
from openswe.slack.responses import WebhookResponse, accepted, ignored
from openswe.users import User
from openswe.utils.preview import skip_on_preview
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks import common
from openswe.workspaces.routing import is_kitchen_channel

logger = logging.getLogger(__name__)

BUTTON_TYPE = "review_offer"


async def _reviewable(pr_ref: GitHubPrRef) -> bool:
    """Whether the pull request is open, not a draft, and could get a review request."""
    if await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number) is not None:
        return False
    try:
        async with PullRequestClient.as_app(pr_ref.owner, pr_ref.repo, pr_ref.number) as pull:
            details = PullRequestPayload.model_validate(await pull.pull())
    except GitHubAppUnavailable:
        return False
    except Exception:
        logger.warning(
            "Could not read a pull request linked in Slack",
            extra={
                "pr_repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}",
                "pr_number": pr_ref.number,
            },
            exc_info=True,
        )
        return False
    return (
        details.state == "open"
        and not details.draft
        and await User.for_login("github", details.author) is not None
    )


def _button(text: str, action: str, thread_ts: str, pr_ref: GitHubPrRef) -> ButtonElement:
    value = {"type": BUTTON_TYPE, "action": action, "thread_ts": thread_ts, "pr_url": pr_ref.url}
    return button(
        text,
        action_id=f"open_swe_option_select_review_offer_{action}",
        value=json.dumps(value),
        style="primary" if action == "request" else None,
    )


async def offer_review(
    channel_id: str, thread_ts: str, reply_thread_ts: str, slack_user_id: str, pr_ref: GitHubPrRef
) -> None:
    """Show the poster a private button to get the pull request they linked reviewed."""
    if skip_on_preview("offer_review"):
        return
    if await User.for_identity("slack", slack_user_id) is None or not await _reviewable(pr_ref):
        return
    text = f"Want Open SWE to get {pr_ref.url} reviewed?"
    await post_slack_ephemeral_message(
        channel_id,
        slack_user_id,
        text,
        reply_thread_ts or None,
        blocks=block_payload(
            [
                section(text),
                actions(
                    _button("Get it reviewed", "request", thread_ts, pr_ref),
                    _button("Dismiss", "dismiss", thread_ts, pr_ref),
                ),
            ]
        ),
    )


async def handle_button(
    interaction: SlackInteraction, button_value: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    """Hand the Slack thread's agent a request to get the pull request reviewed."""
    channel_id = interaction.channel_id
    user_id = interaction.user.id
    pr_ref = parse_github_pr_url(button_value.pr_url)
    if not channel_id or not user_id or not button_value.thread_ts or pr_ref is None:
        return ignored("Missing review offer context")
    if button_value.action == "dismiss":
        background_tasks.add_task(
            respond_to_slack_interaction, interaction.response_url, {"delete_original": True}
        )
        return accepted("Review offer dismissed")
    if button_value.action != "request":
        return ignored("Unknown review offer action")
    if not await common.claim_slack_event(
        f"review-offer:{channel_id}:{button_value.thread_ts}:{pr_ref.url}"
    ):
        return ignored("Review offer already accepted")
    await respond_to_slack_interaction(
        interaction.response_url,
        {"replace_original": True, "text": f"Asked Open SWE to get {pr_ref.url} reviewed."},
    )
    thread_ts = button_value.thread_ts
    channel_context = await common.resolve_slack_channel_context(channel_id)
    thread_id = await common.resolve_slack_thread_id(langgraph_client(), channel_id, thread_ts)
    repo = await common.get_slack_repo_config(
        channel_id,
        thread_ts,
        slack_user_id=user_id,
        channel_context=channel_context,
        thread_id=thread_id,
    )
    action_ts = next((action.action_ts for action in interaction.actions if action.action_ts), "")
    background_tasks.add_task(
        service.process_slack_mention,
        SlackRequest(
            channel_id=channel_id,
            channel_context=channel_context,
            thread_ts=thread_ts,
            event_ts=action_ts or interaction.message_ts or thread_ts,
            user_id=user_id,
            text=f"Get {pr_ref.url} reviewed.",
            bot_user_id=common.SLACK_BOT_USER_ID,
            thread_id=thread_id,
            kitchen_channel=await is_kitchen_channel(channel_id),
            turn_context=prompt("runs/slack-review-offer", pr_url=pr_ref.url),
        ),
        repo,
    )
    return accepted("Review offer accepted")
