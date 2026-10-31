"""Workspace definition validation shared by dashboard and admin tools."""

import asyncio

from openswe.slack.channels import SlackChannel
from openswe.workspaces import refresh
from openswe.workspaces.store import Workspace, WorkspaceUpdate


async def require_kitchen_eligible(channel_ids: list[str]) -> None:
    """Reject newly enabled kitchens Slack would not deliver messages from."""
    channels = await asyncio.gather(
        *(SlackChannel.load(channel_id, use_cache=False) for channel_id in channel_ids)
    )
    for channel_id, channel in zip(channel_ids, channels, strict=True):
        if channel is None or not channel.can_be_kitchen:
            raise ValueError(
                f"Slack channel {channel_id} cannot be a Kitchen or Ask channel: choose an internal "
                "Slack channel that Open SWE has joined."
            )


async def configure_workspace(previous: Workspace, update: WorkspaceUpdate) -> Workspace:
    """Validate a definition update before saving it and synchronizing rebuilds."""
    for field in ("kitchen_channel_ids", "ask_channel_ids"):
        if (channels := getattr(update, field)) is not None:
            already = set(getattr(previous, field))
            await require_kitchen_eligible(
                [channel for channel in channels if channel not in already]
            )
    return await refresh.save_definition(previous, update)
