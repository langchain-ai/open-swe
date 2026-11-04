"""Slack reactions that summon Open SWE or start a kitchen breakout."""

import logging
from dataclasses import dataclass

from openswe.config import ENV
from openswe.input_messages import SystemIdentity
from openswe.prompts import prompt
from openswe.slack import webhook
from openswe.slack.breakout import BreakoutCommand, process_slack_breakout
from openswe.slack.client import SlackThreadMappingError, fetch_slack_thread_message_by_ts
from openswe.slack.code_channels import is_code_channel
from openswe.slack.dm import is_dm_channel
from openswe.slack.events import claim_slack_event
from openswe.slack.failures import SlackRequestError, run_slack_task
from openswe.slack.payloads import SlackChannelContext, SlackEvent
from openswe.slack.records import resolve_slack_thread_id
from openswe.slack.request import SlackRequest
from openswe.slack.thinking import restore_slack_thinking_status
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks import common

logger = logging.getLogger(__name__)

SUMMON_REACTION = "openswe"
BREAKOUT_REACTION = "cooking"
_SUMMON_REACTION_SENDER: SystemIdentity = {
    "id": "system:slack-reaction",
    "display_name": "Slack reaction",
    "platform": "slack",
}


@dataclass(frozen=True)
class SlackReaction:
    request: SlackRequest
    reaction: str

    def first_reactor(self, message: dict[str, object]) -> str:
        reactions = message.get("reactions")
        for reaction in reactions if isinstance(reactions, list) else []:
            if isinstance(reaction, dict) and reaction.get("name") == self.reaction:
                users = reaction.get("users")
                return str(users[0]) if isinstance(users, list) and users else ""
        return ""

    async def dispatch(self) -> None:
        request = self.request
        if not await claim_slack_event(request.event_id, request.channel_id, request.event_ts):
            return
        await restore_slack_thinking_status(request.channel_id, request.thread_ts)
        try:
            thread_id = await resolve_slack_thread_id(
                langgraph_client(), request.channel_id, request.thread_ts
            )
        except SlackThreadMappingError as exc:
            raise SlackRequestError(
                "Open SWE found conflicting state for this Slack thread and will not guess "
                "which agent thread to use."
            ) from exc
        repo = await common.get_slack_repo_config(
            request.channel_id,
            request.thread_ts,
            slack_user_id=request.user_id,
            channel_context=request.channel_context,
            thread_id=thread_id,
        )
        request = request.model_copy(update={"thread_id": thread_id})
        if self.reaction == BREAKOUT_REACTION:
            await process_slack_breakout(
                request, BreakoutCommand(instruction=request.text, kitchen=True), repo
            )
        else:
            await webhook.process_slack_mention(request, repo)


async def process_slack_summon_reaction(
    event: SlackEvent,
    event_id: str,
    *,
    channel_context: SlackChannelContext,
    bot_user_id: str,
    team_id: str,
) -> None:
    """Act for the first reactor on the reacted-to message."""
    if ENV.OPENSWE_ENV.optional() in {"preview", "staging"}:
        return
    item = event.item
    user_id = event.resolve_user_id()
    if (
        event.reaction not in {SUMMON_REACTION, BREAKOUT_REACTION}
        or item is None
        or item.type != "message"
        or not (item.channel and item.ts and user_id and event_id and event.event_ts)
        or user_id == bot_user_id
    ):
        return
    if is_dm_channel(channel_context) or await is_code_channel(item.channel):
        return
    message = await fetch_slack_thread_message_by_ts(item.channel, item.ts, item.ts)
    if message is None:
        return
    thread_ts = str(message.get("thread_ts") or item.ts)
    request = SlackRequest(
        channel_id=item.channel,
        channel_context=channel_context,
        thread_ts=thread_ts,
        event_ts=event.event_ts,
        original_message_ts=item.ts,
        event_id=event_id,
        user_id=user_id,
        text=prompt(
            "slack/reaction-breakout"
            if event.reaction == BREAKOUT_REACTION
            else "slack/reaction-summon",
            reactor=user_id,
            reaction=event.reaction,
            message_ts=item.ts,
        ),
        bot_user_id=bot_user_id,
        trigger_system=_SUMMON_REACTION_SENDER,
        explicit_request=True,
        explicit_mention=True,
        team_id=team_id,
    )
    reaction = SlackReaction(request, event.reaction)
    if reaction.first_reactor(message) != user_id:
        logger.info(
            "Ignoring Slack reaction that is not the first on its message",
            extra={"slack_channel_id": item.channel, "slack_message_ts": item.ts},
        )
        return
    await run_slack_task(request.target, reaction.dispatch())
