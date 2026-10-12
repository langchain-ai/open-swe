"""Collect a person's review description before posting a Slack review request."""

import json
import logging

from fastapi import BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, ValidationError

from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.dispatch import follow_up_configurable
from openswe.human_review.standard import Origin, request_review
from openswe.slack.blocks import (
    actions,
    block_payload,
    button,
    context,
    modal,
    section,
    text_input,
    view_payload,
)
from openswe.slack.client import (
    open_slack_modal,
    parse_github_pr_url,
    post_slack_ephemeral_message,
)
from openswe.slack.payloads import SlackInteraction
from openswe.slack.responses import FeedbackResponse, WebhookResponse, accepted, ignored
from openswe.users import User
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks import common

logger = logging.getLogger(__name__)
CALLBACK_ID = "human_review_request"
BUTTON_TYPE = "human_review_request"
DESCRIPTION = "description"
DESCRIPTION_MAX_CHARS = 1500


class ReviewRequestContext(BaseModel):
    pr_url: str = Field(max_length=250)
    user_id: str = Field(max_length=32)
    channel_id: str = Field(max_length=32)
    thread_ts: str = Field(max_length=32)
    target_channel: str = Field(default="", max_length=80)
    thread_id: str = Field(default="", max_length=36)

    async def open(self, trigger_id: str) -> bool:
        metadata = self.model_dump_json()
        if len(metadata) > 3000:
            raise ValueError("Review description context exceeds Slack's metadata limit")
        view = modal(
            callback_id=CALLBACK_ID,
            title="Request human review",
            submit="Request review",
            close="Cancel",
            private_metadata=metadata,
            blocks=[
                section(f"Selected pull request: {self.pr_url}"),
                text_input(
                    block_id=DESCRIPTION,
                    action_id=DESCRIPTION,
                    label="What should reviewers know?",
                    multiline=True,
                    max_length=DESCRIPTION_MAX_CHARS,
                    placeholder="Explain what changed, why it matters, and where you want attention.",
                ),
                context("Your words will be shared with the review channel."),
            ],
        )
        return bool(trigger_id) and await open_slack_modal(trigger_id, view_payload(view))

    async def offer(self) -> None:
        value = json.dumps(
            {"type": BUTTON_TYPE, "action": "describe", "subject": self.model_dump_json()}
        )
        if len(value) > 2000:
            raise ValueError("Review description context exceeds Slack's button limit")
        text = "Write a description to request human review. Your words will lead the review-channel message."
        await post_slack_ephemeral_message(
            self.channel_id,
            self.user_id,
            text,
            self.thread_ts or None,
            blocks=block_payload(
                [
                    section(text),
                    actions(
                        button(
                            "Describe review request",
                            action_id="open_swe_option_select_describe_review",
                            value=value,
                            style="primary",
                        )
                    ),
                ]
            ),
        )

    async def post(self, description: str) -> None:
        try:
            requester = await User.for_identity("slack", self.user_id)
            pr_ref = parse_github_pr_url(self.pr_url)
            login = requester.login_for("github") if requester is not None else None
            if pr_ref is None or requester is None or not login:
                error = "Link your GitHub account in Open SWE before requesting review."
            else:
                await require_repo_access_for_user(login, f"{pr_ref.owner}/{pr_ref.repo}")
                client = langgraph_client()
                thread_id = self.thread_id or await common.resolve_slack_thread_id(
                    client, self.channel_id, self.thread_ts
                )
                run_config = (
                    follow_up_configurable(
                        thread_metadata(await client.threads.get(thread_id)), thread_id
                    )
                    if thread_id
                    else {}
                )
                run_config.update({"github_login": login, "user_email": requester.email})
                result = await request_review(
                    pr_ref,
                    Origin(
                        requester=requester,
                        thread_id=thread_id or "",
                        run_config=run_config,
                        slack_channel_id=self.channel_id,
                        slack_thread_ts=self.thread_ts,
                    ),
                    channel=self.target_channel,
                    human_description=description,
                )
                if result.success:
                    return
                error = result.error
        except HTTPException as exc:
            error = str(exc.detail)
        except Exception:
            logger.exception("Could not submit human review description")
            error = "Could not request review. Please try again."
        await post_slack_ephemeral_message(
            self.channel_id, self.user_id, error, self.thread_ts or None
        )


async def handle_button(interaction: SlackInteraction, metadata: str) -> WebhookResponse:
    try:
        request = ReviewRequestContext.model_validate_json(metadata)
    except ValidationError:
        logger.warning("Invalid review description context", exc_info=True)
        return ignored("Invalid review description context")
    if request.user_id != interaction.user.id:
        return ignored("Review description belongs to another person")
    if not await request.open(interaction.trigger_id):
        return ignored("Could not open review description modal")
    return accepted("Review description modal opened")


async def handle_submission(
    interaction: SlackInteraction, background_tasks: BackgroundTasks
) -> FeedbackResponse | WebhookResponse:
    try:
        request = ReviewRequestContext.model_validate_json(interaction.view.private_metadata)
    except ValidationError:
        logger.warning("Invalid review description submission context", exc_info=True)
        return ignored("Invalid review description context")
    if request.user_id != interaction.user.id:
        return ignored("Review description belongs to another person")
    description = interaction.view.state.input(DESCRIPTION, DESCRIPTION).value or ""
    if not description.strip() or len(description) > DESCRIPTION_MAX_CHARS:
        return {
            "response_action": "errors",
            "errors": {
                DESCRIPTION: "Describe the change and what you want reviewed (up to 1,500 characters)."
            },
        }
    background_tasks.add_task(request.post, description)
    return {}
