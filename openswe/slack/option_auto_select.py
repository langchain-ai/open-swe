"""Choose a reply's recommended option for the asker when nobody answers in time."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Final

from pydantic import BaseModel

from openswe import event_claims
from openswe.jobs import queue
from openswe.prompts import prompt
from openswe.slack.blocks import selected_option_blocks
from openswe.slack.client import (
    fetch_slack_thread_messages,
    slack_message_bot_id,
    update_slack_message,
)
from openswe.slack.http import SlackRequestError

logger = logging.getLogger(__name__)

AUTO_SELECT_JOB: Final = "slack_option_auto_select"
AUTO_SELECT_DELAY: Final = timedelta(seconds=30)
_CHOICE_SCOPE: Final = "slack_option_choice"
_CHOICE_TTL: Final = timedelta(days=7)


async def claim_option_choice(channel_id: str, message_ts: str) -> bool:
    """Take the one choice a reply's options allow; ``False`` once someone already chose."""
    return await event_claims.claim(_CHOICE_SCOPE, f"{channel_id}:{message_ts}", ttl=_CHOICE_TTL)


class PendingOption(BaseModel):
    """A posted reply whose recommended option is chosen unless someone answers first."""

    channel_id: str
    thread_ts: str
    message_ts: str
    option: str
    user_id: str

    async def schedule(self) -> None:
        await queue.schedule(
            AUTO_SELECT_JOB,
            self.model_dump(),
            due_at=datetime.now(UTC) + AUTO_SELECT_DELAY,
            key=f"{AUTO_SELECT_JOB}:{self.channel_id}:{self.message_ts}",
        )

    async def choose(self) -> bool:
        """Choose the option unless the thread moved on; whether it was chosen."""
        messages = await fetch_slack_thread_messages(self.channel_id, self.thread_ts)
        posted = next((m for m in messages if m.get("ts") == self.message_ts), None)
        if posted is None or any(
            not slack_message_bot_id(m) and str(m.get("ts") or "") > self.message_ts
            for m in messages
        ):
            return False
        if not await claim_option_choice(self.channel_id, self.message_ts):
            return False
        from openswe.slack.thread_owner import wake_thread_owner

        try:
            await wake_thread_owner(
                self.channel_id,
                self.thread_ts,
                self.user_id,
                prompt("slack/option-auto-selected", option=self.option),
            )
        except Exception:
            logger.exception(
                "Could not start a run for an auto-selected option",
                extra={"slack_channel": self.channel_id, "slack_message_ts": self.message_ts},
            )
            await event_claims.release(_CHOICE_SCOPE, f"{self.channel_id}:{self.message_ts}")
            return False
        await self._mark_selected(posted)
        return True

    async def _mark_selected(self, posted: dict[str, object]) -> None:
        raw_blocks = posted.get("blocks")
        blocks = selected_option_blocks(
            [block for block in raw_blocks if isinstance(block, dict)]
            if isinstance(raw_blocks, list)
            else [],
            f"Auto-selected after {AUTO_SELECT_DELAY.seconds}s: {self.option[:150]}",
        )
        if not blocks:
            return
        try:
            await update_slack_message(
                self.channel_id,
                self.message_ts,
                str(posted.get("text") or self.option),
                blocks=blocks,
            )
        except SlackRequestError as exc:
            logger.warning(
                "Could not mark an auto-selected option",
                extra={"slack_channel": self.channel_id, "slack_error": exc.code},
            )
