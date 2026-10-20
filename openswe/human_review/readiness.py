"""Author-only Slack readiness cards, separate from requests for human review."""

import json
import logging
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks, HTTPException
from pydantic import BaseModel, ValidationError

from openswe.dispatch import dispatch_agent_run, follow_up_configurable
from openswe.expedited_review.reviews import github_token_hint
from openswe.github.http import GitHubClient, GitHubSignInRequired
from openswe.github.pull_request_actions import MarkReadyAction, act_on_pull_request
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequestPayload
from openswe.human_review.people import Outcome, linked_participant
from openswe.prompts import prompt
from openswe.slack.blocks import actions, block_payload, button, section
from openswe.slack.client import (
    GitHubPrRef,
    fetch_slack_message_by_ts,
    parse_github_pr_url,
    post_slack_ephemeral_message,
    update_slack_message,
)
from openswe.slack.dm import DmOrigin, note_for_concierge, send_dm_with_location
from openswe.slack.payloads import SlackButtonValue, SlackInteraction, SlackMessage
from openswe.slack.responses import WebhookResponse, accepted, ignored
from openswe.slack.thread_owner import wake_thread_owner
from openswe.users import User
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks import common

if TYPE_CHECKING:
    from openswe.human_review.standard import Origin

logger = logging.getLogger(__name__)
BUTTON_TYPE = "human_review_ready"


class ReadinessCard(BaseModel):
    pr_url: str
    author: str
    slack_user_id: str
    head_sha: str
    thread_id: str = ""
    origin_channel: str = ""
    origin_thread: str = ""

    def value(self) -> str:
        return json.dumps(
            {"type": BUTTON_TYPE, "action": "ready", "fingerprint": self.model_dump_json()}
        )

    async def mark_ready(self, slack_user_id: str) -> None:
        actor = linked_participant(await User.for_person({"id": f"slack:{slack_user_id}"}))
        if (
            isinstance(actor, Outcome)
            or slack_user_id != self.slack_user_id
            or actor.github_login.lower() != self.author.lower()
        ):
            raise ValueError("Only the pull request's author can mark it ready for review.")
        pr = parse_github_pr_url(self.pr_url)
        if pr is None:
            raise ValueError("Invalid pull request on this readiness card.")
        async with GitHubClient.as_user(actor.github_login) as github:
            pull = github.repo(pr.owner, pr.repo).pull_request(pr.number)
            details = PullRequestPayload.model_validate(await pull.pull())
            if details.author.lower() != actor.github_login.lower():
                raise ValueError("Only the pull request's current author can mark it ready.")
            if details.state != "open" or details.merged:
                raise ValueError("This pull request is closed or merged.")
            if details.head_sha != self.head_sha:
                raise ValueError(
                    "New commits changed this pull request. Review the changes carefully and "
                    "ask Open SWE for a new Mark ready button."
                )
            await act_on_pull_request(pull, MarkReadyAction(action="mark-ready"))

    async def notify(self, slack_user_id: str) -> None:
        event = prompt("runs/human-review-ready", pr_url=self.pr_url)
        if self.origin_channel and self.origin_thread:
            await wake_thread_owner(self.origin_channel, self.origin_thread, slack_user_id, event)
        elif self.thread_id:
            thread = await langgraph_client().threads.get(self.thread_id)
            configurable = follow_up_configurable(thread_metadata(thread), self.thread_id)
            await dispatch_agent_run(
                self.thread_id,
                event,
                configurable,
                source=str(configurable.get("source") or "dashboard"),
                thread_title=None,
                context={
                    "sender_id": "system:human-review-ready",
                    "surface": "automation",
                    "kind": "system",
                },
                systems=[
                    {
                        "id": "system:human-review-ready",
                        "display_name": "Human review readiness",
                        "platform": "open-swe",
                    }
                ],
                multitask_strategy="enqueue",
            )


async def offer_readiness(pr: GitHubPrRef, pull: PullRequestClient, origin: Origin) -> str:
    """Send the author a readiness control, without creating a review request."""
    details = PullRequestPayload.model_validate(await pull.pull())
    if not details.draft or details.state != "open" or details.merged:
        return "Refresh the pull request before retrying."
    author = await User.for_login("github", details.author)
    if author is None or not author.slack_user_id:
        return (
            "The author has no linked Slack identity; ask them to use Ready for review on GitHub."
        )
    card = ReadinessCard(
        pr_url=pr.url,
        author=details.author,
        slack_user_id=author.slack_user_id,
        head_sha=details.head_sha,
        thread_id=origin.thread_id,
        origin_channel=origin.slack_channel_id,
        origin_thread=origin.slack_thread_ts,
    )
    text = f"Review {pr.url} carefully yourself before clicking Mark ready."
    blocks = block_payload(
        [
            section(f"*Draft pull request*\n<{pr.url}|{pr.repo}#{pr.number}>"),
            section(text),
            actions(
                button(
                    "Mark ready",
                    action_id="open_swe_option_select_human_review_ready",
                    value=card.value(),
                    style="primary",
                )
            ),
        ]
    )
    location = await send_dm_with_location(
        author.slack_user_id,
        text,
        blocks=blocks,
        origin=DmOrigin(channel_id=origin.slack_channel_id, thread_ts=origin.slack_thread_ts)
        if origin.slack_channel_id and origin.slack_thread_ts
        else None,
    )
    if location is None:
        return (
            "Slack could not deliver the author-only Mark ready button; try again or ask "
            "the author to use Ready for review on GitHub."
        )
    return (
        f"Sent <@{author.slack_user_id}> an author-only Mark ready button in Slack DM. "
        "No review has been requested. Ask the author to review the changes carefully and "
        "click that button; do not send a generic response-option button instead."
    )


async def handle_button(
    interaction: SlackInteraction, value: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    if value.action != "ready" or not interaction.channel_id or not interaction.message_ts:
        return ignored("Missing readiness card context")
    background_tasks.add_task(_process, interaction, value)
    return accepted("Readiness click queued")


async def _process(interaction: SlackInteraction, value: SlackButtonValue) -> None:
    channel, ts, user_id = interaction.channel_id, interaction.message_ts, interaction.user.id
    original = None
    pending_shown = False
    marked = False
    try:
        raw = await fetch_slack_message_by_ts(channel, ts)
        source = SlackMessage.model_validate(raw) if raw is not None else None
        if (
            source is None
            or not common.SLACK_BOT_USER_ID
            or source.user != common.SLACK_BOT_USER_ID
            or source.ts != ts
        ):
            raise ValueError("This readiness card could not be verified. Ask for a new one.")
        original = source.dump()
        blocks = original.get("blocks")
        if not isinstance(blocks, list):
            raise ValueError("This readiness card is no longer active.")
        card = ReadinessCard.model_validate_json(value.fingerprint)
        if not any(
            isinstance(block, dict)
            and block.get("type") == "actions"
            and isinstance(elements := block.get("elements"), list)
            and any(
                isinstance(element, dict) and element.get("value") == card.value()
                for element in elements
            )
            for block in blocks
        ):
            raise ValueError("This readiness card is no longer active.")
        if card.slack_user_id != user_id:
            raise ValueError("Only the pull request's author can mark it ready for review.")
        pending = block_payload([section("Marking ready for review…")])
        await update_slack_message(channel, ts, "Marking ready for review…", blocks=pending)
        pending_shown = True
        await note_for_concierge(user_id, channel, f"Clicked Mark ready for {card.pr_url}.")
        await card.mark_ready(user_id)
        marked = True
        text = f"Marked ready for review: {card.pr_url}"
        await update_slack_message(channel, ts, text, blocks=block_payload([section(text)]))
        await card.notify(user_id)
    except Exception as exc:
        logger.warning("Could not complete a readiness click", exc_info=True)
        if original is not None and pending_shown and not marked:
            try:
                await update_slack_message(
                    channel,
                    ts,
                    str(original.get("text") or ""),
                    blocks=source.dump().get("blocks") if source is not None else None,
                )
            except Exception:
                logger.warning("Could not restore readiness card", exc_info=True)
        if marked:
            error = "Marked ready, but Open SWE could not resume the request. Ask it to request review again."
        elif isinstance(exc, GitHubSignInRequired):
            error = f"Sign in to GitHub before marking ready. {github_token_hint()}"
        elif isinstance(exc, HTTPException):
            error = f"GitHub did not mark the pull request ready: {exc.detail}"
        elif isinstance(exc, ValueError | ValidationError):
            error = str(exc)
        else:
            error = "Could not mark the pull request ready. Try again."
        await post_slack_ephemeral_message(channel, user_id, error, ts)
