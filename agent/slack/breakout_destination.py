"""Resolve breakout destinations from the source workspace."""

from dataclasses import dataclass

from agent.workspaces.routing import resolve_workspace
from agent.workspaces.store import WORKSPACES


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
    if explicit_channel:
        return BreakoutDestination(explicit_channel.strip(), resolved.slug)
    definition = await WORKSPACES.get(resolved.slug)
    return BreakoutDestination(
        definition.breakout_channel_id
        if definition and definition.breakout_channel_id
        else source_channel,
        resolved.slug,
    )
