"""Resolve breakout channels and the source workspace."""

import logging
from dataclasses import dataclass

from openswe.slack.channels import ChannelLookupUnavailable, SlackChannel
from openswe.slack.kitchen_links import KitchenLink
from openswe.workspaces.routing import is_kitchen_channel, resolve_workspace

logger = logging.getLogger(__name__)

LARGE_CHANNEL_MEMBERS = 50


@dataclass(frozen=True)
class BreakoutDestination:
    channel_id: str
    workspace: str

    async def channel_error(self, source_channel: str) -> str | None:
        private_destination_allowed = self.channel_id != source_channel and (
            await is_kitchen_channel(self.channel_id)
        )
        for channel_id in dict.fromkeys((source_channel, self.channel_id)):
            try:
                channel = await SlackChannel.load(channel_id, raise_unavailable=True)
            except ChannelLookupUnavailable as exc:
                logger.info(
                    "Breakout channel verification temporarily unavailable",
                    extra={"slack_channel_id": channel_id, "retry_after_seconds": exc.retry_after},
                )
                return (
                    f"Slack channel verification is temporarily unavailable for <#{channel_id}>. "
                    f"Try again in {exc.retry_after} seconds."
                )
            if channel is None:
                return (
                    f"Could not find or access <#{channel_id}>. "
                    "Check the channel and make sure Open SWE has access."
                )
            if (
                self.channel_id != source_channel
                and not channel.public
                and not (private_destination_allowed and channel_id == self.channel_id)
            ):
                return "Breakouts between channels require a public source channel and destination."
        return None

    async def choices(self, source_channel: str) -> list[str]:
        """Destinations to offer before breaking out to the default; empty to go ahead.

        A channel with a kitchen named after it always offers that kitchen. Otherwise
        a crowded channel, or one whose size Slack won't say, asks to confirm.
        """
        if self.channel_id != source_channel or await is_kitchen_channel(source_channel):
            return []
        if kitchen := await KitchenLink.kitchen_for(source_channel):
            return [kitchen, source_channel]
        channel = await SlackChannel.load(source_channel, use_cache=False)
        count = channel.payload.get("num_members") if channel is not None else None
        if isinstance(count, int) and not isinstance(count, bool) and count < LARGE_CHANNEL_MEMBERS:
            return []
        return [source_channel]


async def resolve_breakout_destination(
    source_channel: str,
    explicit_channel: str | None = None,
    *,
    workspace: str | None = None,
    repo: tuple[str, str] | None = None,
    tag: str | None = None,
    login: str | None = None,
) -> BreakoutDestination:
    resolved = await resolve_workspace(
        thread_workspace=workspace,
        slack_channel_id=source_channel,
        repo=repo,
        tag=tag,
        login=login,
    )
    return BreakoutDestination(
        explicit_channel.strip() if explicit_channel else source_channel, resolved.slug
    )
