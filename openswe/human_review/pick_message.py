"""The DM a reviewer pick's buttons sit on, edited in place as the pick moves on."""

import logging
from collections.abc import Sequence

from pydantic import BaseModel

from openswe.slack.blocks import Block, ButtonElement, actions, block_payload, context, section
from openswe.slack.client import (
    fetch_slack_message_by_ts,
    respond_to_slack_interaction,
    update_slack_message,
)
from openswe.slack.http import SlackRequestError

logger = logging.getLogger(__name__)


class PickMessage(BaseModel):
    """The message a pick's buttons sit on; an edit to it does not notify the reviewer."""

    channel_id: str
    ts: str
    text: str
    # The click's, where the message is updated; the bot token edits it otherwise.
    response_url: str = ""

    @classmethod
    async def fetch(cls, channel_id: str, ts: str) -> PickMessage | None:
        """The message at ``ts``, as Slack has it now."""
        raw = await fetch_slack_message_by_ts(channel_id, ts)
        text = raw.get("text") if raw else None
        return cls(channel_id=channel_id, ts=ts, text=text) if isinstance(text, str) else None

    async def show(self, status: str, buttons: Sequence[ButtonElement] = ()) -> bool:
        blocks: list[Block] = [section(self.text), context(status)]
        if buttons:
            blocks.append(actions(*buttons))
        return await self._update(self.text, blocks)

    async def replace(self, text: str) -> bool:
        return await self._update(text, [section(text)])

    async def _update(self, text: str, blocks: Sequence[Block]) -> bool:
        if self.response_url:
            body = {"replace_original": True, "text": text, "blocks": block_payload(blocks)}
            return await respond_to_slack_interaction(self.response_url, body)
        try:
            await update_slack_message(self.channel_id, self.ts, text, blocks=block_payload(blocks))
        except SlackRequestError as exc:
            logger.warning(
                "Could not update a reviewer pick message",
                extra={"slack_channel": self.channel_id, "slack_error": exc.code},
            )
            return False
        return True
