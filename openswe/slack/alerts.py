"""Operational alerts with a channel-wide cooldown shared by every replica."""

import logging
from collections.abc import Mapping
from datetime import timedelta
from html import escape

from openswe import event_claims
from openswe.slack.http import SlackClient

logger = logging.getLogger(__name__)


class SlackAlertLogger:
    def __init__(self, channel: str) -> None:
        self.channel = channel

    async def warning(self, message: str, *, details: Mapping[str, object]) -> bool:
        """Log every occurrence locally and post at most once per minute to the channel."""
        logger.warning(message, extra=dict(details))
        try:
            if not await event_claims.claim("slack-alert", self.channel, ttl=timedelta(minutes=1)):
                return False
            text = "\n".join(
                [escape(message, quote=False)]
                + [
                    f"{escape(key, quote=False)}: {escape(str(value), quote=False)}"
                    for key, value in details.items()
                ]
            )
            async with SlackClient.bot() as client:
                await client.chat_postMessage(
                    channel=self.channel, text=text, unfurl_links=False, unfurl_media=False
                )
        except Exception:
            logger.exception(
                "Operational Slack alert failed", extra={"slack_channel": self.channel}
            )
            return False
        return True


alerts = SlackAlertLogger("C0C3Y98MEUV")
