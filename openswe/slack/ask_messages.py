"""Jev's judgment of whether an untagged Ask channel message needs a Robin response."""

import logging

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
from openswe.slack.payloads import SlackMessage
from openswe.utils.jev import JevDecision, select_jev_choices
from openswe.webhooks import common
from openswe.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)


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
            transcript = await cls._transcript(messages)
            channel = (await common.resolve_slack_channel_context(channel_id)).model_dump_json(
                include={"name", "topic", "purpose", "description"}
            )
        except Exception:
            logger.warning(
                "Ask channel message check failed; answering the message as usual",
                extra={"slack_channel_id": channel_id, "thread_ts": thread_ts},
                exc_info=True,
            )
            return True
        if not transcript:
            return True
        decision = JevDecision()
        reaction_decision = JevDecision()
        choices = await cls._response(transcript, channel, message_ts, decision, reaction_decision)
        action = choices["action"]
        reaction = choices["reaction"]
        logger.info(
            "Ask channel message judged",
            extra={
                "slack_channel_id": channel_id,
                "thread_ts": thread_ts,
                "ask_action": action,
                "ask_reaction": reaction,
                "jev_outcome": decision.outcome,
                "jev_confidence": decision.confidence,
            },
        )
        if action in {"react_only", "react_and_follow_up"}:
            emoji = reaction if isinstance(reaction, str) and reaction in cls._REACTIONS else "eyes"
            reacted = await add_slack_reaction(channel_id, message_ts, emoji)
            if action == "react_only" and not reacted:
                return True
        return action in {"follow_up_only", "react_and_follow_up"} or (
            decision.outcome == "classifier_failure"
        )

    @classmethod
    async def _response(
        cls,
        transcript: str,
        channel: str,
        message_ts: str,
        decision: JevDecision,
        reaction_decision: JevDecision,
    ) -> dict[str, str | None]:
        instructions = prompt(
            "slack/ask-message-instructions",
            about_robin=prompt("decision-models/robin"),
            channel=channel,
            sent_at=slack_ts_time(message_ts),
            channel_instructions=prompt("runs/slack-ask-channel"),
        )
        return await select_jev_choices(
            transcript,
            decisions={"action": decision, "reaction": reaction_decision},
            min_confidence=0.5,
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
    async def _transcript(cls, messages: list[SlackMessage]) -> str:
        """The conversation as the Slack client shows it."""
        user_ids = [m.user for m in messages if m.user]
        names = await get_slack_user_names(user_ids) if user_ids else {}
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
    def _render(messages: list[SlackMessage], names: dict[str, str]) -> str:
        return format_slack_messages_for_prompt(
            [m.dump() for m in messages],
            names,
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
