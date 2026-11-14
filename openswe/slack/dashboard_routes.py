"""Dashboard API for Slack account linking and the bot allowlist."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import RedirectResponse

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP, session_is_admin
from openswe.dashboard.oauth import session_user_id
from openswe.slack.allowed_bots import (
    AllowedSlackBot,
    AllowSlackBot,
    SlackBotOption,
    allow_slack_bot,
    list_slack_bots,
)
from openswe.slack.channel_options import (
    SlackChannelDirectory,
    list_slack_channels,
    refresh_directory,
)
from openswe.slack.client import get_slack_user_names
from openswe.slack.connect import router as connect_router
from openswe.slack.dm import CONCIERGE_TS, open_dm
from openswe.slack.http import SlackClient, slack_http_errors, slack_identity
from openswe.slack.links import SlackLink
from openswe.slack.records import lookup_slack_thread_id
from openswe.slack.user_options import SlackUserDirectory, SlackUserOption
from openswe.threads.summary import assert_thread_readable
from openswe.users import User
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

router = APIRouter(tags=["slack"])
router.include_router(connect_router)


@router.get("/slack/open", response_class=RedirectResponse, status_code=302)
async def api_open_slack(
    channel: Annotated[str, Query(pattern=r"^[CGD][A-Z0-9]{2,}$", max_length=32)],
    message_ts: Annotated[
        str | None, Query(pattern=r"^[0-9]{1,20}\.[0-9]{1,6}$", max_length=27)
    ] = None,
    thread_ts: Annotated[
        str | None, Query(pattern=r"^[0-9]{1,20}\.[0-9]{1,6}$", max_length=27)
    ] = None,
    _session: dict[str, object] = SESSION_DEP,
) -> RedirectResponse:
    async with slack_http_errors(), SlackClient.bot() as client:
        identity = await slack_identity(client)
    workspace_url = identity.get("url")
    try:
        if not isinstance(workspace_url, str):
            raise ValueError("Slack did not return a workspace URL")
        url = SlackLink(channel, message_ts or "", thread_ts or "").in_workspace(workspace_url)
    except ValueError as exc:
        raise HTTPException(503, "Slack workspace navigation is unavailable. Try again.") from exc
    return RedirectResponse(
        url,
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/slack/users")
async def api_slack_users(
    _session: dict[str, object] = SESSION_DEP,
) -> SlackUserDirectory:
    return SlackUserDirectory(users=await SlackUserOption.all())


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
    thread_id = await lookup_slack_thread_id(channel_id, CONCIERGE_TS) if channel_id else None
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
