"""Resolve breakout channels and the source workspace."""

import logging
from dataclasses import dataclass

from openswe.slack.channels import ChannelLookupUnavailable, SlackChannel
from openswe.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BreakoutDestination:
    channel_id: str
    workspace: str

    async def channel_error(self, source_channel: str) -> str | None:
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
            if self.channel_id != source_channel and not channel.public:
                return "Breakouts between channels require a public source channel and destination."
        return None


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
