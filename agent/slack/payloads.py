"""Typed views of what Slack posts to the webhooks.

Only the fields Open SWE reads are declared; everything else is kept
(``extra="allow"``) so nothing Slack sends is lost on the way through.
"""

import json
import logging
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class SlackPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    @classmethod
    def parse(cls, raw: object) -> Self | None:
        """``None`` when the payload does not have the declared shape."""
        try:
            return cls.model_validate(raw)
        except ValidationError:
            logger.warning("Slack payload has an unexpected shape", exc_info=True)
            return None


class SlackRef(SlackPayload):
    """An object Slack sometimes sends as a bare id and sometimes as ``{"id": ...}``."""

    id: str = ""


class SlackItem(SlackPayload):
    """``event.item``: the reacted-to message, or a code-channel context-bar item."""

    channel: str = ""
    ts: str = ""
    key: str | None = None
    label: str | None = None
    value: JsonValue = None


class SlackMessage(SlackPayload):
    ts: str = ""
    thread_ts: str = ""
    user: str = ""
    text: str | None = None
    subtype: str = ""
    bot_id: str = ""
    attachments: list[JsonObject] = Field(default_factory=list)

    @property
    def is_from_bot(self) -> bool:
        return self.subtype == "bot_message" or bool(self.bot_id)


class SlackEvent(SlackPayload):
    """The ``event`` of an Events API callback, across every event type Open SWE subscribes to."""

    type: str = ""
    subtype: str = ""
    channel: str | SlackRef = ""
    channel_id: str = ""
    channel_type: str = ""
    item: SlackItem | None = None
    ts: str = ""
    event_ts: str = ""
    action_ts: str = ""
    thread_ts: str = ""
    user: str | SlackRef = ""
    user_id: str = ""
    text: str | None = None
    bot_id: str = ""
    attachments: list[JsonObject] = Field(default_factory=list)
    message: SlackMessage | None = None
    previous_message: SlackMessage | None = None
    reaction: str = ""
    action: SlackItem | None = None
    key: str | None = None
    label: str | None = None
    value: JsonValue = None
    team: str = ""

    def resolve_channel_id(self) -> str:
        if isinstance(self.channel, SlackRef):
            return self.channel.id
        if self.channel:
            return self.channel
        if self.item and self.item.channel:
            return self.item.channel
        return self.channel_id

    def resolve_user_id(self) -> str:
        if isinstance(self.user, SlackRef):
            return self.user.id
        return self.user or self.user_id

    @property
    def is_from_bot(self) -> bool:
        return self.subtype == "bot_message" or bool(self.bot_id)


class SlackAuthorization(SlackPayload):
    user_id: str = ""


class SlackEventEnvelope(SlackPayload):
    """An Events API delivery."""

    type: str = ""
    event_id: str = ""
    team_id: str = ""
    challenge: str = ""
    authorizations: list[SlackAuthorization] = Field(default_factory=list)
    authed_users: list[str] = Field(default_factory=list)
    event: SlackEvent | None = None

    def bot_user_id(self, configured: str) -> str:
        """The app's own user id: configured, else whichever the delivery names."""
        if configured:
            return configured
        if self.authorizations and self.authorizations[0].user_id:
            return self.authorizations[0].user_id
        return self.authed_users[0] if self.authed_users else ""


class SlackText(SlackPayload):
    type: str = ""
    text: str = ""


class SlackBlockAction(SlackPayload):
    action_id: str = ""
    type: str = ""
    value: str | None = None
    action_ts: str = ""
    text: SlackText | None = None
    selected_option: JsonValue = None
    selected_options: JsonValue = None
    selected_user: JsonValue = None
    selected_users: JsonValue = None
    selected_conversation: JsonValue = None
    selected_conversations: JsonValue = None
    selected_channel: JsonValue = None
    selected_channels: JsonValue = None
    selected_date: JsonValue = None
    selected_time: JsonValue = None
    selected_date_time: JsonValue = None

    def summary(self) -> JsonObject:
        """The fields worth showing the agent, exactly as Slack sent them."""
        return self.model_dump(
            include=set(SlackBlockAction.model_fields), exclude_unset=True, mode="json"
        )


class SlackInteractionContainer(SlackPayload):
    type: str = ""
    channel_id: str = ""
    view_id: str = ""
    thread_ts: str = ""
    message_ts: str = ""


class SlackInteractionMessage(SlackPayload):
    ts: str = ""
    thread_ts: str = ""
    text: str = ""
    blocks: list[JsonObject] = Field(default_factory=list)


class SlackInteractionUser(SlackPayload):
    id: str = ""
    name: str = ""
    username: str = ""


class SlackInteraction(SlackPayload):
    """A Block Kit interaction (``block_actions``, ``block_suggestion``)."""

    type: str = ""
    trigger_id: str = ""
    action_id: str = ""
    value: str = ""
    container: SlackInteractionContainer = Field(default_factory=SlackInteractionContainer)
    actions: list[SlackBlockAction] = Field(default_factory=list)
    user: SlackInteractionUser = Field(default_factory=SlackInteractionUser)
    channel: SlackRef = Field(default_factory=SlackRef)
    message: SlackInteractionMessage = Field(default_factory=SlackInteractionMessage)

    @property
    def channel_id(self) -> str:
        return self.channel.id or self.container.channel_id

    @property
    def thread_ts(self) -> str:
        return self.message.thread_ts or self.message.ts or self.container.thread_ts

    @property
    def message_ts(self) -> str:
        return self.message.ts or self.container.message_ts


class SlackButtonValue(SlackPayload):
    """The JSON Open SWE packs into a button's ``value`` to know what was clicked."""

    type: str = ""
    action: str = ""
    fingerprint: str = ""
    response: str = ""


def parse_json_object(body: bytes) -> JsonObject | None:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
