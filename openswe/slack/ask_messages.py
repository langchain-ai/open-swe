"""Jev's judgment of whether an untagged Ask channel message needs a Robin response."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from langchain_typesafe import Choice

from openswe.prompts import prompt
from openswe.slack.channels import SlackChannel
from openswe.slack.client import (
    add_slack_reaction,
    fetch_slack_thread_messages,
    format_slack_messages_for_prompt,
    get_slack_user_names,
    slack_ts_time,
)
from openswe.slack.payloads import SlackChannelContext, SlackMessage
from openswe.utils.jev import JEV, JevUnavailable
from openswe.webhooks import common
from openswe.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AskResponse:
    """What Jev says Robin should do with an Ask channel message."""

    ACTIONS: ClassVar[frozenset[str]] = frozenset(
        {"follow_up_only", "do_nothing", "react_only", "react_and_follow_up"}
    )
    FOLLOW_UPS: ClassVar[frozenset[str]] = frozenset({"follow_up_only", "react_and_follow_up"})
    REACTIONS: ClassVar[frozenset[str]] = frozenset({"react_only", "react_and_follow_up"})
    # A missed request costs more than a needless reply, so Robin replies well short of certainty:
    # on the eval this halves missed replies at the cost of about three times the needless ones.
    FOLLOW_UP_AT: ClassVar[float] = 0.4
    # A reaction needs Jev's confident pick.
    CONFIDENT_AT: ClassVar[float] = 0.5

    probabilities: Mapping[str, float] = field(default_factory=dict)
    action: str | None = None
    confidence: float | None = None
    reaction: str | None = None
    unavailable: bool = False

    @property
    def follow_up(self) -> float:
        """How likely Jev finds it that Robin should reply or act, across both follow-up options."""
        return sum(self.probabilities.get(option, 0.0) for option in self.FOLLOW_UPS)

    @property
    def follows_up(self) -> bool:
        # An unavailable classifier answers as Ask channels did before it.
        return self.unavailable or round(self.follow_up, 4) >= self.FOLLOW_UP_AT

    @property
    def reacts(self) -> bool:
        return self.action in self.REACTIONS and (self.confidence or 0.0) >= self.CONFIDENT_AT


class AskChannelMessage:
    """An untagged Ask channel message, answered only when a Robin response is needed."""

    # Jev reads 32k tokens; the transcript gets most of them, the instructions the rest.
    _TRANSCRIPT_MAX_CHARS = 22_000 * 4
    _TRIMMED = "[earlier messages omitted to stay inside the context budget]"
    _CHANNEL_MESSAGES = 30
    _REACTIONS = (
        "nail_care",
        "sparkles",
        "crown",
        "fire",
        "dancer",
        "tea",
        "eyes",
        "skull",
        "face_with_rolling_eyes",
        "rainbow",
        "saluting_face",
        "broken_heart",
        "woman-tipping-hand",
    )

    @classmethod
    async def for_robin(cls, *, channel_id: str, thread_ts: str, message_ts: str) -> bool:
        """Whether Robin answers the message: always outside Ask channels, else Jev decides."""
        try:
            ask_channel = await WORKSPACES.is_ask_channel(channel_id)
        except Exception:
            logger.warning(
                "Ask channel lookup failed; answering the message as usual",
                extra={"slack_channel_id": channel_id},
                exc_info=True,
            )
            return True
        if not ask_channel:
            return True
        try:
            messages = await cls._messages(channel_id, thread_ts, message_ts)
            if not messages or messages[-1].has_attachment:
                return True
            channel = await common.resolve_slack_channel_context(channel_id)
            user_ids = [m.user for m in messages if m.user]
            names = await get_slack_user_names(user_ids) if user_ids else {}
        except Exception:
            logger.warning(
                "Ask channel message check failed; answering the message as usual",
                extra={"slack_channel_id": channel_id, "thread_ts": thread_ts},
                exc_info=True,
            )
            return True
        response = await cls.judge(messages, names, channel)
        logger.info(
            "Ask channel message judged",
            extra={
                "slack_channel_id": channel_id,
                "thread_ts": thread_ts,
                "ask_action": response.action,
                "ask_follow_up": response.follow_up,
                "ask_reaction": response.reaction,
                "jev_confidence": response.confidence,
                "jev_unavailable": response.unavailable,
            },
        )
        if response.reacts:
            reacted = await add_slack_reaction(channel_id, message_ts, response.reaction or "eyes")
            # A reaction meant as the whole answer that cannot be posted becomes a reply.
            if not reacted and not response.follows_up:
                return True
        return response.follows_up

    @classmethod
    async def judge(
        cls, messages: list[SlackMessage], names: Mapping[str, str], channel: SlackChannelContext
    ) -> AskResponse:
        """What Robin should do with the last of `messages`, the one being judged."""
        instructions = prompt(
            "slack/ask-message-instructions",
            about_robin=prompt("decision-models/robin"),
            channel=channel.model_dump_json(include={"name", "topic", "purpose", "description"}),
            sent_at=slack_ts_time(messages[-1].ts),
            channel_instructions=prompt("runs/slack-ask-channel"),
        )
        try:
            response = await JEV.ask(
                cls._transcript(messages, names),
                questions={
                    "action": Choice(
                        instructions=instructions,
                        criteria={
                            "follow_up_only": prompt("slack/ask-message-robin"),
                            "do_nothing": prompt("slack/ask-message-human"),
                            "react_only": prompt("slack/ask-message-react-only"),
                            "react_and_follow_up": prompt("slack/ask-message-react-follow-up"),
                        },
                    ),
                    "reaction": Choice(
                        instructions=instructions,
                        criteria={
                            emoji: prompt("slack/ask-message-reaction", emoji=emoji)
                            for emoji in cls._REACTIONS
                        },
                    ),
                },
            )
        except JevUnavailable:
            return AskResponse(unavailable=True)
        action = response.choices.get("action")
        if action is None or action.choice not in AskResponse.ACTIONS:
            logger.error(
                "Jev gave no valid action",
                extra={"jev_action": action.choice if action else None},
            )
            return AskResponse(unavailable=True)
        reaction = response.choices.get("reaction")
        return AskResponse(
            probabilities=action.probabilities,
            action=action.choice,
            confidence=action.confidence,
            reaction=reaction.choice
            if reaction is not None and reaction.choice in cls._REACTIONS
            else None,
        )

    @classmethod
    async def _messages(
        cls, channel_id: str, thread_ts: str, message_ts: str
    ) -> list[SlackMessage]:
        """The conversation up to the message being judged."""
        if thread_ts == message_ts:
            channel = await SlackChannel.load(channel_id)
            messages = await channel.messages(cls._CHANNEL_MESSAGES) if channel else []
            if all(m.ts != message_ts for m in messages):
                messages += cls._parsed(await fetch_slack_thread_messages(channel_id, message_ts))
        else:
            messages = cls._parsed(await fetch_slack_thread_messages(channel_id, thread_ts))
        messages = [m for m in messages if float(m.ts) <= float(message_ts)]
        return messages if messages and messages[-1].ts == message_ts else []

    @classmethod
    def _transcript(cls, messages: list[SlackMessage], names: Mapping[str, str]) -> str:
        """The conversation as the Slack client shows it."""
        *earlier, judged = messages
        context = (
            cls._render(earlier, names)
            if earlier
            else "(none: this message starts the conversation)"
        )
        return prompt(
            "slack/ask-message-task",
            context=cls._within_budget(context),
            message=cls._render([judged], names),
        )

    @staticmethod
    def _render(messages: list[SlackMessage], names: Mapping[str, str]) -> str:
        return format_slack_messages_for_prompt(
            [m.dump() for m in messages],
            dict(names),
            common.SLACK_BOT_USER_ID,
            "Robin",
            include_thread_replies=True,
            readable_times=True,
        )

    @staticmethod
    def _parsed(raw: list[dict[str, object]]) -> list[SlackMessage]:
        return [m for m in map(SlackMessage.parse, raw) if m is not None and not m.is_noise]

    @classmethod
    def _within_budget(cls, transcript: str) -> str:
        """The newest lines that fit, oldest dropped first."""
        kept: list[str] = []
        remaining = cls._TRANSCRIPT_MAX_CHARS - len(cls._TRIMMED) - 1
        for line in reversed(transcript.splitlines()):
            remaining -= len(line) + 1
            if remaining < 0:
                kept.append(cls._TRIMMED)
                break
            kept.append(line)
        return "\n".join(reversed(kept))
