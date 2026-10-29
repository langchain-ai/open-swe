"""Requester-scoped retries for failed Slack requests and runs."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import jwt
from fastapi import BackgroundTasks
from pydantic import BaseModel, ValidationError

from openswe import event_claims
from openswe.config import ENV
from openswe.prompts import prompt
from openswe.slack.blocks import actions, block_payload, button, section
from openswe.slack.client import (
    fetch_slack_message_by_ts,
    fetch_slack_thread_message_by_ts,
    post_slack_ephemeral_message,
)
from openswe.slack.dm import CONCIERGE_TS
from openswe.slack.failures import SlackRequestError
from openswe.slack.payloads import SlackChannelContext, SlackInteraction, SlackMessage
from openswe.slack.responses import WebhookResponse, accepted, ignored
from openswe.utils.json_types import JsonObject
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks import common
from openswe.workspaces.routing import is_kitchen_channel

logger = logging.getLogger(__name__)
BUTTON_TYPE = "slack_retry"
_RETRY_LIFETIME = timedelta(days=7)


class SlackRetry(BaseModel):
    channel_id: str
    thread_ts: str
    user_id: str
    message_ts: str = ""
    reply_thread_ts: str = ""
    nonce: str

    def blocks(self, text: str) -> list[JsonObject] | None:
        secret = ENV.SLACK_SIGNING_SECRET.get()
        if not secret or not self.user_id or not self.thread_ts:
            return None
        token = jwt.encode(
            {
                **self.model_dump(),
                "aud": BUTTON_TYPE,
                "exp": datetime.now(UTC) + _RETRY_LIFETIME,
            },
            secret,
            algorithm="HS256",
        )
        return block_payload(
            [
                section(text),
                actions(
                    button(
                        "Retry",
                        action_id="open_swe_option_select_retry",
                        value=common.json.dumps({"type": BUTTON_TYPE, "response": token}),
                        style="primary",
                    )
                ),
            ]
        )

    @classmethod
    def parse(cls, token: str) -> SlackRetry | None:
        secret = ENV.SLACK_SIGNING_SECRET.get()
        if not secret:
            return None
        try:
            return cls.model_validate(
                jwt.decode(token, secret, algorithms=["HS256"], audience=BUTTON_TYPE)
            )
        except jwt.InvalidTokenError, ValidationError:
            logger.warning("Invalid or expired Slack retry button", exc_info=True)
            return None

    async def queue(
        self,
        interaction: SlackInteraction,
        channel_context: SlackChannelContext,
        background_tasks: BackgroundTasks,
    ) -> WebhookResponse:
        from openswe.slack import webhook as service
        from openswe.slack.request import SlackRequest

        if self.channel_id != interaction.channel_id or self.user_id != interaction.user.id:
            await post_slack_ephemeral_message(
                interaction.channel_id,
                interaction.user.id,
                "Only the person whose request failed can retry it. No action was taken.",
                interaction.message.thread_ts or None,
            )
            return ignored("Retry belongs to another requester or channel")
        claim_key = f"{self.channel_id}:{self.nonce}"
        if not await event_claims.claim(BUTTON_TYPE, claim_key, ttl=_RETRY_LIFETIME):
            return ignored("Retry already queued")
        try:
            await common.update_slack_message(
                self.channel_id,
                interaction.message_ts,
                interaction.message.text,
                blocks=[
                    block for block in interaction.message.blocks if block.get("type") != "actions"
                ]
                + [{"type": "context", "elements": [{"type": "plain_text", "text": "Retrying…"}]}],
            )
            thread_id = await common.resolve_slack_thread_id(
                langgraph_client(), self.channel_id, self.thread_ts
            )
            repo = await common.get_slack_repo_config(
                self.channel_id,
                self.thread_ts,
                slack_user_id=self.user_id,
                channel_context=channel_context,
                thread_id=thread_id,
            )
            text = "Retry"
            attachments = []
            if self.message_ts:
                raw = (
                    await fetch_slack_message_by_ts(self.channel_id, self.message_ts)
                    if self.thread_ts == CONCIERGE_TS and self.channel_id.startswith("D")
                    else await fetch_slack_thread_message_by_ts(
                        self.channel_id, self.thread_ts, self.message_ts
                    )
                )
                message = SlackMessage.parse(raw)
                if message is None or message.user != self.user_id or message.is_from_bot:
                    raise SlackRequestError(
                        "The original Slack request is no longer available. Send it again to retry."
                    )
                text = message.text or ""
                attachments = message.attachments
            code_channel = await common.is_code_channel(self.channel_id)
            request = SlackRequest(
                channel_id=self.channel_id,
                thread_ts=self.thread_ts,
                event_ts=f"{datetime.now(UTC).timestamp():.6f}",
                event_id=f"slack-retry:{uuid7()}",
                original_message_ts=self.message_ts,
                retry_message_ts=self.message_ts,
                user_id=self.user_id,
                text=text,
                turn_context=prompt("runs/slack-retry").strip() if not self.message_ts else "",
                attachments=attachments,
                bot_user_id=common.SLACK_BOT_USER_ID,
                thread_id=thread_id,
                channel_context=channel_context,
                team_id=interaction.team.id,
                reply_thread_ts=self.reply_thread_ts,
                concierge_mode=self.thread_ts == CONCIERGE_TS and self.channel_id.startswith("D"),
                code_channel=code_channel,
                kitchen_channel=await is_kitchen_channel(self.channel_id),
                treat_all_messages_as_mentions=True,
                explicit_request=True,
            )
            background_tasks.add_task(service.process_slack_mention, request, repo)
        except Exception:
            await event_claims.release(BUTTON_TYPE, claim_key)
            try:
                await common.update_slack_message(
                    self.channel_id,
                    interaction.message_ts,
                    interaction.message.text,
                    blocks=interaction.message.blocks,
                )
            except Exception:
                logger.exception("Could not restore Slack retry button")
            raise
        return accepted("Retry queued")
