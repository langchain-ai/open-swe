"""One Slack message or interaction addressed to Open SWE, as the routes hand it to processing."""

from pydantic import BaseModel, Field

from agent.slack.failures import SlackRequestTarget
from agent.slack.payloads import SlackChannelContext
from agent.utils.json_types import JsonObject


class SlackRequest(BaseModel):
    channel_id: str
    thread_ts: str
    event_ts: str = ""
    original_message_ts: str = ""
    event_id: str = ""
    user_id: str = ""
    user_name: str = ""
    text: str = ""
    attachments: list[JsonObject] = Field(default_factory=list)
    bot_user_id: str = ""
    triggering_bot_id: str = ""
    triggering_bot_app_id: str = ""
    thread_id: str | None = None
    channel_context: SlackChannelContext | None = None
    team_id: str = ""
    reply_thread_ts: str = ""
    # Another Slack thread whose whole transcript becomes this run's context.
    context_thread_ts: str = ""
    treat_all_messages_as_mentions: bool = False
    message_update: bool = False
    code_channel: bool = False
    concierge_mode: bool = False
    explicit_request: bool = False

    @property
    def target(self) -> SlackRequestTarget:
        return SlackRequestTarget(
            channel_id=self.channel_id,
            thread_ts=self.thread_ts,
            event_id=self.event_id,
            agent_thread_id=self.thread_id,
        )
