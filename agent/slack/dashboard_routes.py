"""Dashboard API for Slack account linking and the bot allowlist."""

from typing import Any

from fastapi import APIRouter

from agent.dashboard.deps import ADMIN_DEP, SESSION_DEP, session_is_admin
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

router = APIRouter(tags=["slack"])
router.include_router(connect_router)


@router.get("/slack/bots")
async def api_list_slack_bots(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[SlackBotOption]:
    return await list_slack_bots()


@router.get("/slack/channels")
async def api_list_slack_channels(
    session: dict[str, Any] = SESSION_DEP,
) -> SlackChannelDirectory:
    """Slack channels for the workspace channel picker and ``#`` autocomplete in agent inputs.

    Private channels are listed for admins only.
    """
    directory = await list_slack_channels()
    if session_is_admin(session):
        return directory
    return directory.model_copy(
        update={"channels": [channel for channel in directory.channels if not channel.is_private]}
    )


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
