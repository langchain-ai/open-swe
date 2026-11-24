"""Retire agent reply choices when a Slack conversation moves on."""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Final

from managed_deepagents._channels.slack.api import (  # noqa: PLC2701
    slack_api,
    slack_bot_token,
)
from pydantic import JsonValue

from rt import slack_prompt

logger = logging.getLogger(__name__)

SLACK_CHOICE_MAX_THREADS: Final = 3
SLACK_CHOICE_LOOKBACK_SECONDS: Final = 24 * 60 * 60


@dataclass
class SlackChoiceMessage:
    message: dict[str, JsonValue]

    @classmethod
    async def expire(
        cls,
        history: list[dict[str, JsonValue]],
        *,
        channel_id: str,
        bot_user_id: str,
        before: str,
        concierge: bool,
        context_ts: str,
    ) -> None:
        seen: set[str] = set()
        async for message in cls.conversation_messages(
            history,
            channel_id=channel_id,
            before=before,
            concierge=concierge,
            context_ts=context_ts,
        ):
            if message.get("user") != bot_user_id:
                continue
            ts = message.get("ts")
            if not isinstance(ts, str) or not ts or ts >= before or ts in seen:
                continue
            seen.add(ts)
            choice = cls(message)
            blocks = choice.without_choices()
            if blocks is None:
                continue
            try:
                await slack_api(
                    await slack_bot_token(),
                    "chat.update",
                    {
                        "channel": channel_id,
                        "ts": ts,
                        "text": message.get("text", ""),
                        "blocks": blocks,
                    },
                )
            except Exception:
                logger.exception(
                    "Could not retire Slack choices",
                    extra={"slack_channel_id": channel_id, "slack_message_ts": ts},
                )

    @staticmethod
    async def conversation_messages(
        history: list[dict[str, JsonValue]],
        *,
        channel_id: str,
        before: str,
        concierge: bool,
        context_ts: str,
    ) -> AsyncIterator[dict[str, JsonValue]]:
        """Yield known history and bounded, best-effort scans of the past day in active DM threads."""
        for message in history:
            yield message
        if not concierge:
            return
        parents = (
            history
            if context_ts == "0"
            else await slack_prompt.slack_history(channel_id, "0", max_pages=1)
        )
        threads: list[dict[str, JsonValue]] = []
        for parent in parents:
            yield parent
            ts, count = parent.get("ts"), parent.get("reply_count")
            if (
                isinstance(ts, str)
                and ts
                and ts < before
                and ts != context_ts
                and isinstance(count, int)
                and count > 0
            ):
                threads.append(parent)
        threads.sort(
            key=lambda parent: str(parent.get("latest_reply") or parent["ts"]), reverse=True
        )
        oldest = f"{max(0, slack_prompt.parse_ts(before) - SLACK_CHOICE_LOOKBACK_SECONDS):.6f}"
        for parent in threads[:SLACK_CHOICE_MAX_THREADS]:
            for message in await slack_prompt.slack_history(
                channel_id, str(parent["ts"]), max_pages=1, oldest=oldest, latest=before
            ):
                yield message

    def without_choices(self) -> list[JsonValue] | None:
        blocks = self.message.get("blocks")
        if not isinstance(blocks, list):
            return None
        updated: list[JsonValue] = []
        removed = False
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "actions":
                updated.append(block)
                continue
            elements = block.get("elements")
            if not isinstance(elements, list):
                updated.append(block)
                continue
            remaining = [element for element in elements if not self.is_choice(element)]
            if len(remaining) == len(elements):
                updated.append(block)
            else:
                removed = True
                if remaining:
                    updated.append({**block, "elements": remaining})
        return updated if removed else None

    @staticmethod
    def is_choice(element: JsonValue) -> bool:
        if not isinstance(element, dict) or element.get("type") != "button":
            return False
        value = element.get("value")
        if not isinstance(value, str):
            return False
        try:
            parsed = json.loads(value)
        except ValueError:
            return False
        return isinstance(parsed, dict) and parsed.get("type") == "open_swe_option"
