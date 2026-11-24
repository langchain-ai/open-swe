"""Judge untagged messages against each listening channel's instructions."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import ClassVar

from langchain_typesafe import Choice

from openswe.prompts import prompt
from openswe.routing.store import INTEGRATIONS
from openswe.slack.channels import SlackChannel
from openswe.slack.client import (
    add_slack_reaction,
    fetch_slack_thread_messages,
    format_slack_messages_for_prompt,
    get_slack_user_names,
    slack_ts_time,
)
from openswe.slack.payloads import SlackChannelContext, SlackMessage
from openswe.slack.records import SlackThread
from openswe.utils.jev import JEV, JevUnavailable
from openswe.webhooks import common

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AskResponse:
    """What Jev says Robin should do with a listening-channel message."""

    ACTIONS: ClassVar[frozenset[str]] = frozenset(
        {"follow_up_only", "do_nothing", "react_only", "react_and_follow_up"}
    )
    FOLLOW_UPS: ClassVar[frozenset[str]] = frozenset({"follow_up_only", "react_and_follow_up"})
    REACTIONS: ClassVar[frozenset[str]] = frozenset({"react_only", "react_and_follow_up"})
    # A missed request costs more than a needless reply, so Robin replies well short of certainty:
    # on the eval this halves missed replies at the cost of about three times the needless ones.
    FOLLOW_UP_AT: ClassVar[float] = 0.4
    HUMAN_FOLLOW_UP_AT: ClassVar[float] = 0.7
    # A reaction needs Jev's confident pick.
    CONFIDENT_AT: ClassVar[float] = 0.5

    probabilities: Mapping[str, float] = field(default_factory=dict)
    action: str | None = None
    confidence: float | None = None
    reaction: str | None = None
    unavailable: bool = False
    follow_up_at: float = FOLLOW_UP_AT

    @property
    def follow_up(self) -> float:
        """How likely Jev finds it that Robin should reply or act, across both follow-up options."""
        return sum(self.probabilities.get(option, 0.0) for option in self.FOLLOW_UPS)

    @property
    def follows_up(self) -> bool:
        # An unavailable classifier answers as Ask channels did before it.
        return self.unavailable or round(self.follow_up, 4) >= self.follow_up_at

    @property
    def reacts(self) -> bool:
        return self.action in self.REACTIONS and (self.confidence or 0.0) >= self.CONFIDENT_AT


class AskChannelMessage:
    """An untagged listening-channel message, answered when a Robin response is needed."""

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
        """Whether an untagged message is for this Robin, using its configured channel prompt."""
        judgment = await cls._judgment(channel_id, thread_ts, message_ts)
        judgment.log()
        return judgment.answered

    @classmethod
    async def _judgment(cls, channel_id: str, thread_ts: str, message_ts: str) -> ListeningJudgment:
        judgment = partial(
            ListeningJudgment, channel_id=channel_id, thread_ts=thread_ts, message_ts=message_ts
        )
        if thread_ts != message_ts:
            other_robin = await SlackThread.another_robin_started(
                channel_id, thread_ts, bot_user_id=common.SLACK_BOT_USER_ID
            )
            if other_robin is None:
                return judgment(reason=JudgmentReason.ORIGIN_UNVERIFIED, answered=False)
            if other_robin:
                return judgment(reason=JudgmentReason.ANOTHER_ROBIN, answered=False)
        try:
            channel_prompt = await INTEGRATIONS.channel_prompt(channel_id)
        except Exception:
            logger.warning(
                "Listening channel lookup failed; answering the message as usual",
                extra={"slack_channel_id": channel_id},
                exc_info=True,
            )
            return judgment(reason=JudgmentReason.LOOKUP_FAILED, answered=True)
        if channel_prompt is None:
            return judgment(reason=JudgmentReason.NOT_LISTENING, answered=True)
        try:
            messages = await cls._messages(channel_id, thread_ts, message_ts)
            if not messages:
                return judgment(reason=JudgmentReason.UNREADABLE, answered=True)
            if messages[-1].has_attachment:
                return judgment(reason=JudgmentReason.ATTACHMENT, answered=True)
            channel = await common.resolve_slack_channel_context(channel_id)
            user_ids = [m.user for m in messages if m.user]
            names = await get_slack_user_names(user_ids) if user_ids else {}
        except Exception:
            logger.warning(
                "Listening channel message check failed; answering the message as usual",
                extra={"slack_channel_id": channel_id, "thread_ts": thread_ts},
                exc_info=True,
            )
            return judgment(reason=JudgmentReason.UNREADABLE, answered=True)
        response = await cls.judge(messages, names, channel, channel_prompt=channel_prompt)
        reacted = False
        answered = response.follows_up
        if response.reacts:
            reacted = await add_slack_reaction(channel_id, message_ts, response.reaction or "eyes")
            # A reaction meant as the whole answer that cannot be posted becomes a reply.
            answered = answered or not reacted
        return judgment(
            reason=JudgmentReason.JUDGED,
            answered=answered,
            response=response,
            reacted=reacted,
        )

    @classmethod
    async def judge(
        cls,
        messages: list[SlackMessage],
        names: Mapping[str, str],
        channel: SlackChannelContext,
        *,
        channel_prompt: str = "",
    ) -> AskResponse:
        """What Robin should do with the last of `messages`, the one being judged."""
        instructions = prompt(
            "slack/ask-message-instructions",
            about_robin=prompt("decision-models/robin"),
            channel=channel.model_dump_json(include={"name"}),
            sent_at=slack_ts_time(messages[-1].ts),
            channel_instructions=channel_prompt,
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
        previous = messages[-2] if len(messages) > 1 else None
        judged = messages[-1]
        follows_human = (
            bool(judged.thread_ts)
            and judged.thread_ts != judged.ts
            and previous is not None
            and bool(previous.user)
            and previous.user != common.SLACK_BOT_USER_ID
            and not previous.is_from_bot
        )
        return AskResponse(
            follow_up_at=AskResponse.HUMAN_FOLLOW_UP_AT
            if follows_human
            else AskResponse.FOLLOW_UP_AT,
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


class JudgmentReason(StrEnum):
    """What decided whether Robin answered an untagged message."""

    JUDGED = "judged"
    ANOTHER_ROBIN = "another_robin"
    ORIGIN_UNVERIFIED = "origin_unverified"
    NOT_LISTENING = "not_listening"
    LOOKUP_FAILED = "lookup_failed"
    UNREADABLE = "unreadable"
    ATTACHMENT = "attachment"


@dataclass(frozen=True)
class ListeningJudgment:
    """The routing decision for one untagged Slack message."""

    channel_id: str
    thread_ts: str
    message_ts: str
    reason: JudgmentReason
    answered: bool
    response: AskResponse | None = None
    reacted: bool = False

    def log(self) -> None:
        """Emit the decision without message text or channel instructions."""
        response = self.response
        logger.info(
            "Listening channel message judged",
            extra={
                "slack_channel_id": self.channel_id,
                "thread_ts": self.thread_ts,
                "slack_message_ts": self.message_ts,
                "ask_reason": self.reason.value,
                "ask_answered": self.answered,
                "ask_action": response.action if response else None,
                "ask_action_probabilities": dict(response.probabilities) if response else {},
                "ask_follow_up": response.follow_up if response else None,
                "ask_follow_up_at": response.follow_up_at if response else None,
                "ask_reaction": (response.reaction or "eyes")
                if response and response.reacts
                else None,
                "ask_reacted": self.reacted,
                "jev_confidence": response.confidence if response else None,
                "jev_unavailable": response.unavailable if response else False,
            },
        )
