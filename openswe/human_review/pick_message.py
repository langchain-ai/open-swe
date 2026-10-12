"""The DM a reviewer pick's buttons sit on, edited in place as the pick moves on."""

import logging
from collections.abc import Sequence

from pydantic import BaseModel

from openswe.slack.blocks import Block, ButtonElement, actions, block_payload, context, section
from openswe.slack.client import update_slack_message
from openswe.slack.http import SlackRequestError

logger = logging.getLogger(__name__)


class PickMessage(BaseModel):
    """The message a pick's buttons sit on; an edit to it does not notify the reviewer."""

    channel_id: str
    ts: str
    text: str

    async def show(self, status: str, buttons: Sequence[ButtonElement] = ()) -> bool:
        blocks: list[Block] = [section(self.text), context(status)]
        if buttons:
            blocks.append(actions(*buttons))
        try:
            await update_slack_message(
                self.channel_id, self.ts, self.text, blocks=block_payload(blocks)
            )
        except SlackRequestError as exc:
            logger.warning(
                "Could not update a reviewer pick message",
                extra={"slack_channel": self.channel_id, "slack_error": exc.code},
            )
            return False
        return True
