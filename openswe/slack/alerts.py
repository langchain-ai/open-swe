"""Operational alerts with a channel-wide cooldown shared by every replica."""

import logging
from collections.abc import Mapping
from datetime import timedelta
from html import escape

from openswe import event_claims
from openswe.config import ENV
from openswe.slack.blocks import block_payload, context, section
from openswe.slack.http import SlackClient

logger = logging.getLogger(__name__)


class SlackAlertLogger:
    def __init__(self, channel: str | None = None) -> None:
        self.channel = channel

    async def warning(
        self,
        message: str,
        *,
        details: Mapping[str, object],
        slack_message: str | None = None,
        cooldown: timedelta = timedelta(minutes=1),
    ) -> bool:
        """Log locally and post within the channel-wide cooldown shared by all replicas."""
        logger.warning(message, extra=dict(details))
        channel = self.channel or ENV.SLACK_ALERTS_CHANNEL_ID.optional()
        if not channel:
            return False
        try:
            if not await event_claims.claim("slack-alert", channel, ttl=cooldown):
                return False
            text = f"⚠️ *{escape(slack_message or message, quote=False)}*"
            detail_text = "\n".join(
                f"*{escape(key, quote=False)}:* {escape(str(value), quote=False)}"
                for key, value in details.items()
                if value is not None
            )
            async with SlackClient.bot() as client:
                await client.chat_postMessage(
                    channel=channel,
                    text=text,
                    blocks=block_payload(
                        [section(text), *([context(detail_text)] if detail_text else [])]
                    ),
                    unfurl_links=False,
                    unfurl_media=False,
                )
        except Exception:
            logger.exception("Operational Slack alert failed", extra={"slack_channel": channel})
            return False
        return True


alerts = SlackAlertLogger()
