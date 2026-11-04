"""Admin-authorized tools for the Slack bot allowlist."""

from fastapi import HTTPException

from openswe.audit_logs.tools import audit_tool
from openswe.slack.allowed_bots import (
    AllowedSlackBot,
    AllowSlackBot,
)
from openswe.slack.allowed_bots import (
    allow_slack_bot as allow_bot,
)
from openswe.slack.allowed_bots import (
    list_slack_bots as available_bots,
)
from openswe.tools.access import Policy, access, ack
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp

_READ = Policy(trusted="admin_surface", actor="admin", direct=True)
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack(), direct=True)


@expose_mcp(access="admin")
@access(_READ)
async def list_slack_bots() -> dict[str, object]:
    """List available Slack bots and the current allowlist."""
    return {
        "available": [bot.model_dump(mode="json") for bot in await available_bots()],
        "allowed": [bot.model_dump(mode="json") for bot in await AllowedSlackBot.all()],
    }


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def allow_slack_bot(bot_id: str) -> dict[str, object]:
    """Allow one active Slack bot to trigger Open SWE."""
    login = configurable().github_login
    if not login:
        return {"ok": False, "error": "A signed-in admin is required."}
    try:
        bot = await allow_bot(AllowSlackBot(bot_id=bot_id), {"sub": login})
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "bot": bot.model_dump(mode="json")}


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def remove_slack_bot(team_id: str, bot_id: str) -> dict[str, object]:
    """Revoke one Slack bot's permission to trigger Open SWE."""
    await AllowedSlackBot.remove(team_id, bot_id)
    return {"ok": True}
