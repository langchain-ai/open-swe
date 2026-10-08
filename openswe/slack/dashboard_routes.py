"""Dashboard API for Slack account linking and the bot allowlist."""

from typing import Annotated, Any

from fastapi import APIRouter, Path

from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP, session_is_admin
from openswe.slack.allowed_bots import (
    ALLOWED_SLACK_BOTS,
    AllowedSlackBot,
    AllowedSlackBotEntry,
    AllowSlackBot,
    SlackBotOption,
    allow_slack_bot,
    allowed_slack_bot_directory,
    list_slack_bots,
)
from openswe.slack.channel_options import SlackChannelDirectory, list_slack_channels
from openswe.slack.client import get_slack_user_names, lookup_slack_thread_id
from openswe.slack.connect import router as connect_router
from openswe.slack.dm import CONCIERGE_TS, open_dm
from openswe.threads.summary import assert_thread_readable
from openswe.users import User
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

router = APIRouter(tags=["slack"])
router.include_router(connect_router)


@router.get("/slack/users/{user_id}/name")
async def api_slack_user_name(
    user_id: Annotated[str, Path(pattern=r"^[UW][A-Z0-9]{2,}$", max_length=32)],
    _session: dict[str, object] = SESSION_DEP,
) -> dict[str, str]:
    names = await get_slack_user_names([user_id])
    return {"name": names.get(user_id, user_id)}


@router.get("/slack/concierge")
async def api_concierge(
    session: dict[str, str] = SESSION_DEP,
) -> dict[str, str | None]:
    user = await User.for_login("github", session["sub"])
    if user is None or not user.slack_user_id:
        return {"thread_id": None, "channel_id": None}
    channel_id = await open_dm(user.slack_user_id)
    thread_id = (
        await lookup_slack_thread_id(langgraph_client(), channel_id, CONCIERGE_TS)
        if channel_id
        else None
    )
    if thread_id:
        thread = await langgraph_client().threads.get(thread_id)
        assert_thread_readable(thread_metadata(thread), session["sub"], session.get("email"))
    return {"thread_id": thread_id, "channel_id": channel_id}


@router.get("/slack/bots")
async def api_list_slack_bots(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[SlackBotOption]:
    return await list_slack_bots()


@router.get("/slack/channels")
async def api_list_slack_channels(
    session: dict[str, Any] = SESSION_DEP,
    refresh: bool = False,
) -> SlackChannelDirectory:
    """Slack channels for the workspace channel picker and ``#`` autocomplete in agent inputs.

    Private channels are listed for admins only.
    """
    directory = await list_slack_channels(refresh=refresh)
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


@router.get("/slack/allowed-bots/directory")
async def api_allowed_slack_bot_directory(
    _session: dict[str, Any] = SESSION_DEP,
) -> list[AllowedSlackBotEntry]:
    """Names and avatars of allowed bots, for the Bots view every user can open."""
    return await allowed_slack_bot_directory()


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
