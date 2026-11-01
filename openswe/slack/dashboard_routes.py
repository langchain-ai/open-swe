"""Dashboard API for Slack account linking and the bot allowlist."""

from typing import Annotated, Any

from fastapi import APIRouter, Path

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP, session_is_admin
from openswe.dashboard.oauth import session_user_id
from openswe.slack.allowed_bots import (
    AllowedSlackBot,
    AllowedSlackBotEntry,
    AllowSlackBot,
    SlackBotOption,
    allow_slack_bot,
    allowed_slack_bot_directory,
    list_slack_bots,
)
from openswe.slack.channel_options import (
    SlackChannelDirectory,
    list_slack_channels,
    refresh_directory,
)
from openswe.slack.client import get_slack_user_names
from openswe.slack.connect import router as connect_router
from openswe.slack.dm import concierge_thread_id, open_dm
from openswe.threads import index
from openswe.threads.summary import assert_thread_readable
from openswe.users import User

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
    user = await User.for_session(session_user_id(session), session["sub"])
    if user is None or not user.typed_preferences.concierge_mode or not user.slack_user_id:
        return {"thread_id": None, "channel_id": None}
    channel_id = await open_dm(user.slack_user_id)
    thread_id = await concierge_thread_id(channel_id) if channel_id else None
    # A pre-MDA conversation's fresh thread exists once the next DM creates it;
    # until then the sidebar links to the DM.
    thread = await index.get(thread_id) if thread_id else None
    if thread is None:
        return {"thread_id": None, "channel_id": channel_id}
    assert_thread_readable(thread.metadata, session["sub"], session.get("email"))
    return {"thread_id": thread_id, "channel_id": channel_id}


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
    return _visible_channels(await list_slack_channels(), session)


@router.post("/slack/channels/refresh")
async def api_refresh_slack_channels(
    session: dict[str, Any] = SESSION_DEP,
) -> SlackChannelDirectory:
    """Walk Slack's channels again now; the directory reloads when the walk ends."""
    await refresh_directory(by=session_user_id(session))
    return _visible_channels(await list_slack_channels(), session)


def _visible_channels(
    directory: SlackChannelDirectory, session: dict[str, Any]
) -> SlackChannelDirectory:
    if session_is_admin(session):
        return directory
    return directory.model_copy(
        update={"channels": [channel for channel in directory.channels if not channel.is_private]}
    )


@router.get("/slack/allowed-bots")
async def api_list_allowed_slack_bots(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[AllowedSlackBot]:
    return await AllowedSlackBot.all()


@router.get("/slack/allowed-bots/directory")
async def api_allowed_slack_bot_directory(
    _session: dict[str, Any] = SESSION_DEP,
) -> list[AllowedSlackBotEntry]:
    """Names and avatars of allowed bots, for the Bots view every user can open."""
    return await allowed_slack_bot_directory()


@router.post("/slack/allowed-bots")
@audit_endpoint
async def api_allow_slack_bot(
    body: AllowSlackBot,
    admin: dict[str, Any] = ADMIN_DEP,
) -> AllowedSlackBot:
    return await allow_slack_bot(body, admin)


@router.delete("/slack/allowed-bots/{team_id}/{bot_id}")
@audit_endpoint
async def api_remove_allowed_slack_bot(
    team_id: str,
    bot_id: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, bool]:
    await AllowedSlackBot.remove(team_id, bot_id)
    return {"ok": True}
