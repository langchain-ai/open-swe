from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from agent.dashboard.admin import is_admin
from agent.dashboard.oauth import require_session
from agent.dashboard.threads.listing import list_dashboard_thread_slack_channels
from agent.slack.channels import SlackChannelSummary

slack_channels_router = APIRouter(prefix="/threads", tags=["dashboard"])
_Session = Annotated[dict[str, object], Depends(require_session)]


@slack_channels_router.get("/slack-channels")
async def list_thread_slack_channels(
    session: _Session,
    include_resolved: bool = False,
    include_automations: bool = False,
    all: bool = False,
) -> list[SlackChannelSummary]:
    if all and not is_admin(
        session.get("email") if isinstance(session.get("email"), str) else None,
        login=session.get("sub") if isinstance(session.get("sub"), str) else None,
    ):
        raise HTTPException(403, "admin only")
    login = session.get("sub")
    if not isinstance(login, str):
        raise HTTPException(401, "invalid session")
    email = session.get("email")
    return await list_dashboard_thread_slack_channels(
        login,
        email=email if isinstance(email, str) else None,
        include_resolved=include_resolved,
        include_automations=include_automations,
        include_all=all,
    )
