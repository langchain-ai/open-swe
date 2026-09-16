"""The `manage_incident` tool: follow the current Slack channel as an incident, or turn it off."""

from typing import Any, Literal

from agent.incidents import channels, service
from agent.incidents.models import Incident, IncidentPolicy
from agent.incidents.runtime import current_run_id
from agent.run_config import RunConfig
from agent.slack.client import get_slack_channel_info
from agent.utils.dashboard_links import dashboard_incident_url

IncidentAction = Literal["start", "pause", "resume", "complete"]

# action -> {current status: control to apply}; a missing status means nothing changes.
_CONTROLS: dict[str, dict[str, str]] = {
    "start": {"paused": "resume", "completed": "reopen"},
    "pause": {"watching": "pause", "needs_attention": "pause"},
    "resume": {"paused": "resume", "completed": "reopen"},
    "complete": {"watching": "complete", "needs_attention": "complete", "paused": "complete"},
}


async def manage_incident(action: IncidentAction) -> dict[str, Any]:
    """Implement the `manage_incident` tool."""
    cfg = RunConfig.from_runtime()
    channel_id = cfg.slack_thread.channel_id if cfg.slack_thread else ""
    if not channel_id:
        return {"success": False, "error": "This conversation is not in a Slack channel"}
    policy = await service.get_policy()
    if not policy.enabled:
        return {
            "success": False,
            "error": (
                "Incidents is not enabled in this workspace; an admin can enable it under "
                "Admin → Incidents"
            ),
        }
    record = await service.INCIDENTS.get(service.incident_id(policy.workspace_id, channel_id))
    if record is None:
        if action != "start":
            return {
                "success": False,
                "error": "This channel is not an incident; use start to follow it",
            }
        return await _start(cfg, policy, channel_id)
    if record.is_archived:
        return {
            "success": False,
            "error": "This channel is archived, so its incident cannot change",
        }
    control = _CONTROLS[action].get(record.status)
    if control is None:
        return _result(record, action, changed=False)
    # Both sides must name a real run: a dashboard completion records no run id and
    # current_run_id() is empty outside a run, which must not read as a match.
    closed_by_this_run = (
        bool(record.completed_run_id) and record.completed_run_id == current_run_id()
    )
    if control == "reopen" and closed_by_this_run:
        return {
            "success": False,
            "error": (
                "This run just completed the incident; it cannot reopen it. A responder can "
                "mention me with reopen if it should keep going."
            ),
            "status": record.status,
            "incident_id": record.id,
        }
    # The current run is the one carrying out the request, so it must keep running.
    record = await channels.apply_control(
        record, control, _actor(cfg), keep_run_id=current_run_id()
    )
    return _result(record, action, changed=True)


async def _start(cfg: RunConfig, policy: IncidentPolicy, channel_id: str) -> dict[str, Any]:
    if not cfg.github_login:
        return {
            "success": False,
            "error": (
                "Starting an incident needs a connected Open SWE account "
                "(Sign in with Slack in the dashboard)"
            ),
        }
    info = await get_slack_channel_info(channel_id, use_cache=False)
    if info is None or not service.channel_allowed(info, policy, require_prefix=False):
        return {
            "success": False,
            "error": "Incidents can only follow a public internal channel that is not excluded",
        }
    record = await channels.enroll_channel(
        channel_id, str(info.get("name") or channel_id), policy, manual=True
    )
    if record is None:
        existing = await service.INCIDENTS.get(service.incident_id(policy.workspace_id, channel_id))
        if existing is None:
            return {"success": False, "error": "Incident enrollment did not complete"}
        return _result(existing, "start", changed=False)
    if record.status == "needs_attention":
        return {
            "success": False,
            "error": "Channel setup failed; the incident's dashboard page has the details",
            "incident_id": record.id,
        }
    return _result(record, "start", changed=True)


def _actor(cfg: RunConfig) -> dict[str, Any]:
    user = cfg.slack_thread.triggering_user_id if cfg.slack_thread else ""
    if user:
        return {"id": f"slack:{user}", "platform": "slack"}
    if cfg.github_login:
        return {"id": f"github:{cfg.github_login}", "platform": "github"}
    return {"id": "agent:incidents", "platform": "open-swe"}


def _result(record: Incident, action: str, *, changed: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": True,
        "action": action,
        "changed": changed,
        "status": record.status,
        "channel_id": record.channel_id,
        "incident_id": record.id,
    }
    url = dashboard_incident_url(record.id)
    if url:
        result["dashboard_url"] = url
    if changed:
        result["note"] = "The channel has been notified; do not post a duplicate status message."
    return result
