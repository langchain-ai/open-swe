"""Resolve breakout channels and the source workspace."""

from dataclasses import dataclass

from openswe.slack.channels import SlackChannel
from openswe.workspaces.routing import resolve_workspace


@dataclass(frozen=True)
class BreakoutDestination:
    channel_id: str
    workspace: str

    async def channel_error(
        self, source_channel: str, *, check_audience: bool = False
    ) -> str | None:
        for channel_id in dict.fromkeys((source_channel, self.channel_id)):
            channel = await SlackChannel.load(channel_id, use_cache=False)
            if channel is None:
                return f"Could not verify access to <#{channel_id}>."
            if self.channel_id != source_channel and not channel.public:
                return "Breakouts between channels require a public source channel and destination."
            if check_audience and channel_id == self.channel_id:
                count = channel.payload.get("num_members")
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    return (
                        "I couldn't verify this channel's size. Please choose a breakout "
                        "destination explicitly, including this channel if you want to stay here."
                    )
                if count >= 50:
                    return (
                        f"This channel has {count} members. Would you like the breakout here "
                        "or in a quieter, relevant channel? Please choose a destination "
                        "explicitly before I create it."
                    )
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
