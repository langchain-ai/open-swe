"""Typed views of what Slack posts to the webhooks.

Only the fields Open SWE reads are declared; everything else is kept
(``extra="allow"``) so nothing Slack sends is lost on the way through.
"""

import json
import logging
from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


NOISE_SUBTYPES = frozenset(
    {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name"}
)


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
    app_id: str = ""
    reply_count: int = 0
    attachments: list[JsonObject] = Field(default_factory=list)

    @property
    def is_from_bot(self) -> bool:
        return self.subtype == "bot_message" or bool(self.bot_id)

    @property
    def sort_key(self) -> float:
        """The timestamp as a number, for ordering oldest first."""
        try:
            return float(self.ts)
        except ValueError:
            return 0.0

    @property
    def is_noise(self) -> bool:
        """A join or topic-change notice, which carries nothing a reader wants."""
        return self.subtype in NOISE_SUBTYPES

    def dump(self) -> JsonObject:
        """The raw-shaped mapping the prompt formatter reads.

        Unset fields are dropped rather than defaulted, so a message with no text
        still reads as one with no text.
        """
        return self.model_dump(mode="json", exclude_none=True)


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
    app_id: str = ""
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
    api_app_id: str = ""
    challenge: str = ""
    authorizations: list[SlackAuthorization] = Field(default_factory=list)
    authed_users: list[str] = Field(default_factory=list)
    event: SlackEvent | None = None

    @property
    def kind(self) -> str:
        """The inner event's type for a callback, else the envelope's own type."""
        return self.event.type if self.event and self.event.type else self.type

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


class SlackSelectedOption(SlackPayload):
    value: str = ""


class SlackInputValue(SlackPayload):
    """One element's current value, as a view's or message's ``state.values`` carries it."""

    value: str | None = None
    selected_options: list[SlackSelectedOption] = Field(default_factory=list)


class SlackViewState(SlackPayload):
    values: dict[str, dict[str, SlackInputValue]] = Field(default_factory=dict)


class SlackView(SlackPayload):
    id: str = ""
    callback_id: str = ""
    private_metadata: str = ""
    state: SlackViewState = Field(default_factory=SlackViewState)


class SlackModalOrigin(SlackPayload):
    """The channel a modal was opened from, as Open SWE packs it into ``private_metadata``."""

    channel_id: str = ""


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
    state: SlackViewState = Field(default_factory=SlackViewState)
    view: SlackView = Field(default_factory=SlackView)

    @property
    def channel_id(self) -> str:
        return self.channel.id or self.container.channel_id

    @property
    def origin_channel_id(self) -> str:
        """``channel_id``, or for a modal submission the channel its metadata names."""
        if self.channel_id or not self.view.private_metadata:
            return self.channel_id
        try:
            return SlackModalOrigin.model_validate_json(self.view.private_metadata).channel_id
        except ValidationError:
            logger.debug("Slack modal metadata names no origin channel", exc_info=True)
            return ""

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
    thread_id: str = ""
    thread_ts: str = ""
    response: str = ""


class SlackViewSubmission(SlackPayload):
    """A submitted modal: which view it was, who submitted it, and what they typed."""

    type: str = ""
    trigger_id: str = ""
    view: SlackView = Field(default_factory=SlackView)
    user: SlackInteractionUser = Field(default_factory=SlackInteractionUser)
    team: SlackRef = Field(default_factory=SlackRef)

    @property
    def callback_id(self) -> str:
        return self.view.callback_id

    @property
    def metadata(self) -> JsonObject:
        """``private_metadata`` parsed as JSON; ``{}`` when it is absent or malformed."""
        if not self.view.private_metadata:
            return {}
        return parse_json_object(self.view.private_metadata.encode()) or {}

    def selected(self, block_id: str, action_id: str) -> list[str]:
        block = self.view.state.values.get(block_id) or {}
        element = block.get(action_id)
        return [option.value for option in element.selected_options] if element else []

    def submitted(self, block_id: str, action_id: str) -> str:
        """What was typed into one input, or ``""`` when it was left empty."""
        block = self.view.state.values.get(block_id) or {}
        element = block.get(action_id)
        return (element.value or "") if element is not None else ""


def parse_json_object(body: bytes) -> JsonObject | None:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class SlackChannelContext(SlackPayload):
    """A channel's identity and description, as prompts and thread metadata carry it.

    Sharing flags stay ``None`` when Slack did not say, because the operations
    gate has to fail closed on an unknown rather than assume the safe answer.
    """

    id: str = ""
    name: str = ""
    name_normalized: str = ""
    topic: str = ""
    purpose: str = ""
    description: str = ""
    is_ext_shared: bool | None = None
    is_pending_ext_shared: bool | None = None
    is_im: bool | None = None
    is_mpim: bool | None = None

    def dump(self) -> JsonObject:
        """The JSON value to store in thread metadata."""
        return self.model_dump(mode="json")

    @property
    def allows_operations(self) -> bool:
        """Whether Slack confirms this channel is not externally shared."""
        return self.is_im is True or (
            self.is_ext_shared is False and self.is_pending_ext_shared is False
        )

    @property
    def description_text(self) -> str:
        """Prompt-safe description text."""
        if self.description.strip():
            return self.description.strip()
        return "\n".join(value.strip() for value in (self.topic, self.purpose) if value.strip())

    @property
    def has_metadata(self) -> bool:
        """Whether any name or description field carries something."""
        return any(
            value.strip()
            for value in (
                self.name,
                self.name_normalized,
                self.topic,
                self.purpose,
                self.description,
            )
        )

    @property
    def label(self) -> str:
        """The channel's display name, either spelling, else ``""``."""
        return self.name or self.name_normalized


class SlackChannelPayload(SlackPayload):
    """A typed read of Slack's ``channel`` object.

    Slack sends ``topic`` and ``purpose`` as ``{"value": ...}`` and, in some
    payloads, as bare strings; both flatten to the text here. A flag that is not
    a boolean becomes ``None`` so callers cannot read a shrug as a yes.
    """

    id: str = ""
    name: str = ""
    name_normalized: str = ""
    topic: str = ""
    purpose: str = ""
    is_channel: bool | None = None
    is_private: bool | None = None
    is_im: bool | None = None
    is_mpim: bool | None = None
    is_ext_shared: bool | None = None
    is_pending_ext_shared: bool | None = None

    @classmethod
    def of(cls, raw: object) -> Self:
        """A view of ``raw``; an empty one when it is missing or malformed."""
        if not isinstance(raw, Mapping):
            return cls()
        return cls.parse(raw) or cls()

    @field_validator("topic", "purpose", mode="before")
    @classmethod
    def _section_text(cls, raw: object) -> str:
        if isinstance(raw, Mapping):
            value = raw.get("value")
            return value.strip() if isinstance(value, str) else ""
        return raw.strip() if isinstance(raw, str) else ""

    @field_validator(
        "is_channel",
        "is_private",
        "is_im",
        "is_mpim",
        "is_ext_shared",
        "is_pending_ext_shared",
        mode="before",
    )
    @classmethod
    def _only_boolean(cls, raw: object) -> bool | None:
        return raw if isinstance(raw, bool) else None

    @property
    def is_public(self) -> bool:
        """Whether anybody in the workspace can already read this channel.

        Channel history is fetched with the deployment's bot token, which says
        nothing about who is asking, so only a channel with no membership to leak
        may be read that way: not private, not a DM or group DM, and not shared
        with another organization.
        """
        return (
            self.is_channel is True
            and self.is_private is False
            and self.is_im is not True
            and self.is_mpim is not True
            and self.is_ext_shared is False
            and self.is_pending_ext_shared is False
        )

    @property
    def topic_and_purpose(self) -> str:
        """Topic and purpose joined into one description string."""
        return "\n".join(value for value in (self.topic, self.purpose) if value)

    def to_context(self, channel_id: str) -> SlackChannelContext:
        return SlackChannelContext(
            id=channel_id,
            name=self.name.strip(),
            name_normalized=self.name_normalized.strip(),
            topic=self.topic,
            purpose=self.purpose,
            description=self.topic_and_purpose,
            is_ext_shared=self.is_ext_shared,
            is_pending_ext_shared=self.is_pending_ext_shared,
            is_im=self.is_im,
            is_mpim=self.is_mpim,
        )
