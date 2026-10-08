"""The DM a reviewer pick's buttons sit on."""

import logging
from collections.abc import Sequence

from pydantic import BaseModel

from openswe.slack.blocks import Block, ButtonElement, actions, block_payload, context, section
from openswe.slack.client import update_slack_message
from openswe.slack.http import SlackRequestError

logger = logging.getLogger(__name__)


class PickMessage(BaseModel):
    """A pick's message, which shows a click's progress or why the pick ended in place of its buttons."""

    channel_id: str
    ts: str
    text: str

    async def show(self, status: str, buttons: Sequence[ButtonElement] = ()) -> None:
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
