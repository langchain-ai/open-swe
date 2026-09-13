"""Incident policy, records, dashboard projections, and responder commands."""

import hashlib
import json
import logging
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import ValidationError

from agent.config import ENV
from agent.incidents.evidence_tools import redact
from agent.incidents.models import Activity, Incident, IncidentPolicy, IncidentReportRecord
from agent.input_messages import PersonIdentity
from agent.slack.client import get_slack_channel_info
from agent.slack.http import slack_client
from agent.store import TypedStore, now_iso
from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)
POLICIES = TypedStore(["incidents", "policies"], IncidentPolicy)
INCIDENTS = TypedStore(["incidents", "incidents"], Incident)
REPORTS = TypedStore(["incidents", "reports"], IncidentReportRecord)
ACTIVE_STATUSES = frozenset({"watching", "needs_attention"})
_VIEWS = {"active": ACTIVE_STATUSES, "inactive": frozenset({"paused", "completed"})}
REQUIRED_SLACK_SCOPES = frozenset(
    {
        "channels:read",
        "channels:join",
        "channels:history",
        "app_mentions:read",
        "chat:write",
        "users:read",
        "users:read.email",
    }
)


def incident_id(workspace_id: str, channel_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"open-swe:incidents:{workspace_id}:{channel_id}"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def redact_context(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value, 8000)
    if isinstance(value, list):
        return [redact_context(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_context(item) for key, item in value.items()}
    return value


def note(target: Incident | IncidentReportRecord, kind: str, text: str) -> None:
    if not target.activity or target.activity[-1].summary != text:
        target.activity.append(Activity(type=kind, summary=text))
        target.activity = target.activity[-100:]


async def save(record: Incident) -> None:
    from agent.incidents import documents

    record.updated_at = now_iso()
    await INCIDENTS.put(record.id, record)
    await documents.preserve_metadata(record)


async def get_policy() -> IncidentPolicy:
    return await POLICIES.get("default") or IncidentPolicy()


def channel_allowed(
    channel: dict[str, Any], policy: IncidentPolicy, *, for_read: bool = False
) -> bool:
    public_internal = (
        channel.get("is_channel") is True
        and channel.get("is_private") is False
        and channel.get("is_im") is not True
        and channel.get("is_mpim") is not True
        and channel.get("is_ext_shared") is False
        and channel.get("is_pending_ext_shared") is False
    )
    if not public_internal:
        return False
    if for_read:
        return channel.get("is_member") is True
    return (
        str(channel.get("name", "")).startswith(policy.channel_prefix)
        and channel.get("id") not in policy.excluded_channel_ids
        and channel.get("is_archived") is not True
    )


async def readable(
    record: Incident, policy: IncidentPolicy, *, raise_on_unavailable: bool = False
) -> bool:
    """Whether responders may read this incident right now: the bot must still be a member."""
    if (
        record.workspace_id != policy.workspace_id
        or record.channel_id in policy.excluded_channel_ids
    ):
        return False
    info = await get_slack_channel_info(record.channel_id, use_cache=False)
    if info is None:
        if raise_on_unavailable:
            raise HTTPException(
                503, "Slack access verification is temporarily unavailable. Try again."
            )
        return False
    return channel_allowed(info, policy, for_read=True)


async def _auth_test() -> tuple[dict[str, Any], list[str] | None]:
    async with slack_client(token=ENV.SLACK_BOT_TOKEN.get()) as client:
        response = await client.auth_test()
    data = response.data if isinstance(response.data, dict) else {}
    header = response.headers.get("x-oauth-scopes") if response.headers else None
    scopes = [scope.strip() for scope in header.split(",")] if isinstance(header, str) else None
    return data, scopes


def _last_operation(policy: IncidentPolicy) -> dict[str, Any]:
    return {"command_id": f"settings:{policy.version}", "status": "applied", "error": None}


async def get_settings() -> dict[str, Any]:
    policy = await get_policy()
    app_id = ENV.SLACK_APP_ID.get()
    connection: dict[str, Any] = {
        "slack_configured": bool(ENV.SLACK_BOT_TOKEN.get()),
        "workspace_id": policy.workspace_id,
        "slack_app_id": app_id or policy.slack_app_id,
        "required_scopes_present": None,
        "verified_at": None,
        "error": None,
    }
    if connection["slack_configured"]:
        try:
            auth, scopes = await _auth_test()
            team_id = str(auth.get("team_id") or "")
            if team_id:
                connection["workspace_id"] = team_id
            if policy.workspace_id and team_id != policy.workspace_id:
                connection["error"] = "Slack installation belongs to a different workspace."
            else:
                connection["verified_at"] = now_iso()
            if scopes is not None:
                connection["required_scopes_present"] = REQUIRED_SLACK_SCOPES.issubset(scopes)
                if not connection["required_scopes_present"]:
                    connection["error"] = "Slack installation is missing required Incidents scopes."
        except Exception:
            connection["error"] = "Slack verification failed; check installation and permissions."
    if not app_id:
        connection["error"] = (
            connection["error"] or "Set SLACK_APP_ID to the installed Slack app ID."
        )
    elif policy.slack_app_id and app_id != policy.slack_app_id:
        connection["error"] = (
            connection["error"] or "SLACK_APP_ID differs from the app bound to this policy."
        )
    return {
        "policy": policy.model_dump(),
        "connection": connection,
        "last_operation": _last_operation(policy),
    }


async def update_settings(
    policy: dict[str, Any], expected_version: int, actor: dict[str, Any]
) -> dict[str, str]:
    current = await get_policy()
    if expected_version != current.version:
        raise HTTPException(409, "Settings changed; reload before saving")
    try:
        candidate = IncidentPolicy.model_validate(policy)
    except ValidationError as exc:
        raise HTTPException(
            422,
            "Invalid incident policy: "
            + "; ".join(error["msg"] for error in exc.errors(include_input=False)),
        ) from exc
    # Workspace and app identity are server-owned: the bot token's workspace and
    # SLACK_APP_ID. Client-supplied values are ignored.
    candidate.workspace_id = current.workspace_id
    candidate.slack_app_id = current.slack_app_id
    if candidate.model:
        from agent.dashboard.options import SUPPORTED_MODEL_IDS

        if candidate.model not in SUPPORTED_MODEL_IDS:
            raise HTTPException(422, "Unsupported incident model")
    if candidate.enabled:
        app_id = ENV.SLACK_APP_ID.get()
        if not app_id:
            raise HTTPException(
                422, "Set SLACK_APP_ID to the installed Slack app ID before enabling Incidents"
            )
        try:
            auth, _ = await _auth_test()
        except Exception as exc:
            raise HTTPException(
                422, "Slack verification failed; check installation and permissions"
            ) from exc
        workspace_id = str(auth.get("team_id") or "")
        if not workspace_id:
            raise HTTPException(422, "Slack installation did not report a workspace")
        bound = (current.workspace_id, current.slack_app_id)
        identity = (workspace_id, app_id)
        if bound != identity and any(bound) and await INCIDENTS.search(limit=1):
            raise HTTPException(
                409, "Slack workspace or app cannot change with registered incidents"
            )
        candidate.workspace_id, candidate.slack_app_id = identity
    candidate.version = current.version + 1
    candidate.enabled_at = (
        time.time() if candidate.enabled and not current.enabled else current.enabled_at
    )
    await POLICIES.put("default", candidate)
    logger.info(
        "Incident settings updated", extra={"actor": actor.get("id"), "version": candidate.version}
    )
    return {"command_id": f"settings:{candidate.version}", "status": "applied"}


def summary(record: Incident, latest: IncidentReportRecord | None) -> dict[str, Any]:
    return {
        "id": record.id,
        "channel_id": record.channel_id,
        "channel_name": record.channel_name,
        "title": record.title or record.channel_name,
        "is_archived": record.is_archived,
        "status": record.status,
        "reason": record.reason,
        "latest_finding": latest.report.summary if latest else "",
        "updated_at": max(record.updated_at, latest.updated_at) if latest else record.updated_at,
        "slack_url": f"https://slack.com/archives/{record.channel_id}",
    }


def setup_summary(record: Incident) -> dict[str, Any]:
    return {
        "id": record.id,
        "channel_id": record.channel_id,
        "channel_name": "",
        "title": "Channel setup needs attention",
        "is_archived": False,
        "status": record.status,
        "reason": record.reason,
        "latest_finding": "",
        "updated_at": record.updated_at,
        "slack_url": "",
    }


async def list_incidents(
    view: str | None = None,
    q: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
    *,
    include_setup: bool = False,
) -> dict[str, Any]:
    policy = await get_policy()
    records = sorted(await INCIDENTS.search_all(), key=lambda r: r.updated_at, reverse=True)
    items = []
    for record in records:
        if record.workspace_id != policy.workspace_id:
            continue
        if view and view != "all" and record.status not in _VIEWS.get(view, {view}):
            continue
        if await readable(record, policy):
            item = summary(record, await REPORTS.get(record.id))
        elif include_setup and record.reason == "setup_failed":
            item = setup_summary(record)
        else:
            continue
        if q and q.lower() not in f"{item['title']} {item['channel_name']}".lower():
            continue
        items.append(item)
    try:
        offset = int(cursor or 0)
        if offset < 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor") from exc
    limit = max(1, min(limit, 100))
    return {
        "items": items[offset : offset + limit],
        "next_cursor": str(offset + limit) if len(items) > offset + limit else None,
    }


async def get_incident(id: str, *, include_setup: bool = False) -> dict[str, Any]:
    from agent.incidents import turns

    record = await INCIDENTS.get(id)
    policy = await get_policy()
    if not record or record.workspace_id != policy.workspace_id:
        raise HTTPException(404, "Incident not found")
    setup_failed = record.reason == "setup_failed"
    if not await readable(
        record, policy, raise_on_unavailable=not (include_setup and setup_failed)
    ):
        if include_setup and setup_failed:
            return {
                "incident": setup_summary(record),
                "report": None,
                "coverage": {
                    "gaps": [
                        "Channel setup did not complete. Check Slack grants and configuration."
                    ]
                },
                "activity": [item.model_dump() for item in reversed(record.activity)],
                "allowed_actions": [],
                "trace_url": None,
                "next_cursor": None,
            }
        raise HTTPException(404, "Incident not found")
    latest = await REPORTS.get(id)
    status: str = record.status
    if record.status in ACTIVE_STATUSES and await turns.has_active_run(record.thread_id):
        status = "investigating"
    actions = ["ask"]
    if record.status != "completed":
        actions.append("investigate_again")
    if record.status == "completed":
        if not record.is_archived:
            actions.append("reopen")
    elif record.status == "paused":
        actions.extend(["resume", "complete"])
    else:
        actions.extend(["pause", "complete"])
    activity = sorted(
        [*record.activity, *(latest.activity if latest else [])],
        key=lambda item: item.at,
        reverse=True,
    )[:100]
    return {
        "incident": {**summary(record, latest), "status": status},
        "report": latest.report.model_dump() if latest else None,
        "coverage": {"gaps": latest.report.gaps if latest else []},
        "activity": [item.model_dump() for item in activity],
        "allowed_actions": actions,
        "trace_url": await get_langsmith_trace_url(record.thread_id) if record.thread_id else None,
        "next_cursor": None,
    }


def requester_identity(actor: dict[str, Any]) -> PersonIdentity:
    login = str(actor.get("github_login") or actor.get("id") or "responder").replace(" ", "-")
    person: PersonIdentity = {"id": f"github:{login}", "platform": "github", "github_login": login}
    person["display_name"] = login
    email = actor.get("email")
    if isinstance(email, str) and email:
        person["email"] = email
    return person


async def submit_command(
    id: str, action: str, text: str | None, request_id: str, actor: dict[str, Any]
) -> dict[str, str]:
    from agent.incidents import channels, turns

    detail = await get_incident(id)
    if action not in detail["allowed_actions"]:
        raise HTTPException(409, "Action is not available in this incident state")
    if action == "ask" and (not text or not text.strip() or len(text) > 8000):
        raise HTTPException(422, "A question of 1–8000 characters is required")
    record = await INCIDENTS.get(id)
    assert record is not None
    policy = await get_policy()
    if action == "ask":
        assert text is not None
        await turns.dispatch_turn(
            record, policy, request=text.strip(), requester=requester_identity(actor)
        )
    elif action == "investigate_again":
        await turns.dispatch_turn(record, policy)
    else:
        await channels.apply_control(record, action, actor)
    return {"command_id": request_id, "status": "accepted"}
