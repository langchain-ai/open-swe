"""Dashboard API for Slack account linking and the bot allowlist."""

from typing import Any

from fastapi import APIRouter

from agent.dashboard.deps import ADMIN_DEP
from agent.slack.allowed_bots import (
    ALLOWED_SLACK_BOTS,
    AllowedSlackBot,
    AllowSlackBot,
    SlackBotOption,
    allow_slack_bot,
    list_slack_bots,
)
from agent.slack.channel_options import SlackChannelDirectory, list_slack_channels
from agent.slack.connect import router as connect_router
from agent.slack.untagged_channels import (
    UNTAGGED_CHANNELS,
    SetUntaggedChannel,
    UntaggedChannel,
)

router = APIRouter(tags=["slack"])
router.include_router(connect_router)


@router.get("/slack/bots")
async def api_list_slack_bots(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[SlackBotOption]:
    return await list_slack_bots()


@router.get("/slack/channels")
async def api_list_slack_channels(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> SlackChannelDirectory:
    """The channels a workspace can be bound to, for the picker on the Workspaces page."""
    return await list_slack_channels()


@router.get("/slack/untagged-channels")
async def api_list_untagged_channels(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[UntaggedChannel]:
    return await UNTAGGED_CHANNELS.search_all()


@router.post("/slack/untagged-channels")
async def api_enable_untagged_channel(
    body: SetUntaggedChannel,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> UntaggedChannel:
    return await UNTAGGED_CHANNELS.put(body.channel_id, UntaggedChannel(channel_id=body.channel_id))


@router.delete("/slack/untagged-channels/{channel_id}")
async def api_disable_untagged_channel(
    channel_id: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, bool]:
    await UNTAGGED_CHANNELS.delete(SetUntaggedChannel(channel_id=channel_id).channel_id)
    return {"ok": True}


@router.get("/slack/allowed-bots")
async def api_list_allowed_slack_bots(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[AllowedSlackBot]:
    return await ALLOWED_SLACK_BOTS.search_all()


@router.post("/slack/allowed-bots")
async def api_allow_slack_bot(
    body: AllowSlackBot,
    admin: dict[str, Any] = ADMIN_DEP,
) -> AllowedSlackBot:
    return await allow_slack_bot(body, admin)


@router.delete("/slack/allowed-bots/{team_id}/{bot_id}")
async def api_remove_allowed_slack_bot(
    team_id: str,
    bot_id: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, bool]:
    await ALLOWED_SLACK_BOTS.delete(f"{team_id}:{bot_id}")
    return {"ok": True}
