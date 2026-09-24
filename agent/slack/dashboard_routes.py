"""Dashboard API for Slack account linking, the bot allowlist, and channel configs."""

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
from agent.slack.channel_config import (
    SlackChannelConfig,
    SlackChannelConfigUpdate,
    SlackChannelConfigView,
)
from agent.slack.channel_options import SlackChannelDirectory, list_slack_channels
from agent.slack.connect import router as connect_router

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


@router.get("/slack/channel-configs")
async def api_list_slack_channel_configs(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[SlackChannelConfigView]:
    return [SlackChannelConfigView.model_validate(row) for row in await SlackChannelConfig.all()]


@router.put("/slack/channel-configs/{channel_id}")
async def api_save_slack_channel_config(
    channel_id: str,
    body: SlackChannelConfigUpdate,
    admin: dict[str, Any] = ADMIN_DEP,
) -> SlackChannelConfigView:
    saved = await SlackChannelConfig.save(channel_id, body, admin["sub"])
    return SlackChannelConfigView.model_validate(saved)


@router.delete("/slack/channel-configs/{channel_id}")
async def api_remove_slack_channel_config(
    channel_id: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, bool]:
    await SlackChannelConfig.remove(channel_id)
    return {"ok": True}
