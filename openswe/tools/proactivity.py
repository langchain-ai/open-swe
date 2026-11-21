"""The tool for accepting an offer the proactivity engine cleared."""

from fastapi import HTTPException
from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.proactivity.proactivity_engine import OfferRefused, ProactivityEngine
from openswe.tools.access import Policy, access, ack
from openswe.utils.json_types import as_json_object

_OWN_PRIVATE_THREAD = Policy(trusted="private", actor="owner", sole=ack(), direct=True)


@audit_tool()
@access(_OWN_PRIVATE_THREAD)
async def accept_proactive_offer(
    playbook: str, details: str = "", cron: str | None = None, timezone: str = "UTC"
) -> dict[str, object]:
    """Implement the `accept_proactive_offer` tool."""
    config = get_config()
    engine = await ProactivityEngine.for_login(
        await resolve_github_login(as_json_object(config)) or ""
    )
    if engine is None:
        return {"ok": False, "error": "Proactivity is not on for this person here"}
    try:
        automation = await engine.accepted(playbook, details=details, cron=cron, timezone=timezone)
    except OfferRefused as exc:
        return {"ok": False, "error": str(exc)}
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    return {"ok": True, "automation": automation}
