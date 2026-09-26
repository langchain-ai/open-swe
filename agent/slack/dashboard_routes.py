"""Dashboard API for Slack account linking and the bot allowlist."""

from typing import Any

from fastapi import APIRouter, HTTPException

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
from agent.slack.channels import SlackChannel
from agent.slack.connect import router as connect_router
from agent.slack.kitchen_channels import (
    KITCHEN_CHANNELS,
    KitchenChannel,
    SetKitchenChannel,
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


@router.get("/slack/kitchen-channels")
async def api_list_kitchen_channels(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[KitchenChannel]:
    return await KITCHEN_CHANNELS.search_all()


@router.post("/slack/kitchen-channels")
async def api_enable_kitchen_channel(
    body: SetKitchenChannel,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> KitchenChannel:
    channel = await SlackChannel.load(body.channel_id, use_cache=False)
    if (
        channel is None
        or not channel.context.allows_operations
        or channel.payload.get("is_member") is not True
    ):
        raise HTTPException(400, "Choose an internal Slack channel that Open SWE has joined.")
    return await KITCHEN_CHANNELS.put(body.channel_id, KitchenChannel(channel_id=body.channel_id))


@router.delete("/slack/kitchen-channels/{channel_id}")
async def api_disable_kitchen_channel(
    channel_id: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, bool]:
    await KITCHEN_CHANNELS.delete(SetKitchenChannel(channel_id=channel_id).channel_id)
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
