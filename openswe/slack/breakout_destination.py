"""Resolve breakout channels and the source workspace."""

from dataclasses import dataclass

from openswe.workspaces.routing import resolve_workspace


@dataclass(frozen=True)
class BreakoutDestination:
    channel_id: str
    workspace: str


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
