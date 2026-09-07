"""Durable receipt acceptance and authenticated dashboard operations."""

import hashlib
import json
import logging
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException
from pydantic import ValidationError

from agent.config import ENV
from agent.dispatch import create_durable_run
from agent.investigations import slack
from agent.investigations.models import (
    CoordinatorState,
    Investigation,
    InvestigationPolicy,
    Publication,
    Receipt,
)
from agent.store import TypedStore, now_iso, store_client
from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)
POLICIES = TypedStore(["investigate", "policies"], InvestigationPolicy)
INVESTIGATIONS = TypedStore(["investigate", "investigations"], Investigation)
RECEIPTS = TypedStore(["investigate", "receipts"], Receipt)
PUBLICATIONS = TypedStore(["investigate", "publications"], Publication)
COORDINATORS = TypedStore(["investigate", "coordinators"], CoordinatorState)


def investigation_id(workspace_id: str, channel_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"open-swe:investigate:{workspace_id}:{channel_id}"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def redact_context(value: Any) -> Any:
    from agent.investigations.evidence_tools import redact

    if isinstance(value, str):
        return redact(value, 8000)
    if isinstance(value, list):
        return [redact_context(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_context(item) for key, item in value.items()}
    return value


def receipt_payload(event: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(event).encode()) > 256 * 1024:
        raise HTTPException(413, "Investigation event exceeds 256 KiB")
    fields = {
        "type",
        "subtype",
        "channel",
        "event_ts",
        "ts",
        "thread_ts",
        "user",
        "bot_id",
        "app_id",
        "bot_profile",
        "metadata",
        "text",
        "attachments",
        "blocks",
        "message",
        "deleted_ts",
    }
    return redact_context({key: value for key, value in event.items() if key in fields})


async def get_policy() -> InvestigationPolicy:
    return await POLICIES.get("default") or InvestigationPolicy()


async def _wake() -> None:
    thread_id = investigation_id("coordinator", "default")
    await store_client().threads.create(
        thread_id=thread_id,
        if_exists="do_nothing",
        metadata={"source": "investigate_coordinator"},
    )
    await create_durable_run(
        thread_id,
        "investigate_coordinator",
        input={},
        source="investigate_coordinator",
        metadata={"source": "investigate_coordinator"},
        multitask_strategy="enqueue",
    )


async def wake() -> None:
    try:
        await _wake()
    except Exception:
        # The durable inbox remains available to the recovery tick.
        logger.exception("Investigation dispatch deferred to recovery")


async def accept_slack_event(payload: dict[str, Any]) -> dict[str, str] | None:
    event = payload.get("event") or {}
    kind = event.get("type")
    if kind not in {
        "channel_created",
        "channel_rename",
        "channel_archive",
        "message",
        "app_mention",
    }:
        return None
    policy = await get_policy()
    channel = event.get("channel")
    channel_id = channel.get("id", "") if isinstance(channel, dict) else channel
    if not isinstance(channel_id, str) or not channel_id:
        return None
    record = await INVESTIGATIONS.get(investigation_id(policy.workspace_id, channel_id))
    if record and record.reason == "code_channel":
        return None
    enrollment = kind in {"channel_created", "channel_rename"}
    awaiting_registration = False
    if not record and policy.enabled and not enrollment:
        awaiting_registration = any(
            receipt.kind in {"channel_created", "channel_rename"}
            and receipt.workspace_id == policy.workspace_id
            and receipt.source_time >= policy.enabled_at
            for receipt in await RECEIPTS.search_all(filter={"channel_id": channel_id})
        )
    if not record and not awaiting_registration and not (policy.enabled and enrollment):
        return None
    if (
        payload.get("team_id") != policy.workspace_id
        or payload.get("api_app_id") != policy.slack_app_id
    ):
        raise HTTPException(401, "Slack workspace or app does not match Investigate policy")
    if record and (record.expired or not policy.enabled):
        return {"status": "ignored"}
    try:
        source_time = float(payload.get("event_time") or event.get("event_ts") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Invalid event timestamp") from exc
    if not record and source_time < policy.enabled_at:
        return {"status": "ignored"}
    if (
        not record
        and isinstance(channel, dict)
        and not str(channel.get("name", "")).startswith(policy.channel_prefix)
    ):
        return {"status": "ignored"}
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise HTTPException(400, "Slack event ID is required")
    normalized = receipt_payload(event)
    body_hash = fingerprint(event)
    receipt_id = fingerprint([policy.workspace_id, "slack", event_id])
    existing = await RECEIPTS.get(receipt_id)
    if existing:
        if existing.content_hash != body_hash:
            raise HTTPException(409, "Event ID was reused with different content")
        return {"status": "duplicate"}
    await RECEIPTS.put(
        receipt_id,
        Receipt(
            id=receipt_id,
            workspace_id=policy.workspace_id,
            channel_id=channel_id,
            kind=str(kind),
            payload=normalized,
            source_time=source_time,
            received_at=time.time(),
            content_hash=body_hash,
        ),
    )
    await wake()
    return {"status": "accepted"}


async def ensure_recovery() -> None:
    client = store_client()
    existing = await client.crons.search(metadata={"source": "investigate_recovery"}, limit=1)
    if not existing:
        await client.crons.create(
            "scheduler",
            schedule="* * * * *",
            input={"task": "investigate"},
            metadata={"source": "investigate_recovery"},
        )


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


async def get_settings() -> dict[str, Any]:
    policy = await get_policy()
    state = await COORDINATORS.get("default") or CoordinatorState()
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
            auth = await slack.request("auth.test")
            team_id = str(auth.get("team_id") or "")
            if team_id:
                connection["workspace_id"] = team_id
            if policy.workspace_id and team_id != policy.workspace_id:
                connection["error"] = "Slack installation belongs to a different workspace."
            else:
                connection["verified_at"] = now_iso()
            if "granted_scopes" in auth:
                connection["required_scopes_present"] = REQUIRED_SLACK_SCOPES.issubset(
                    auth["granted_scopes"]
                )
                if not connection["required_scopes_present"]:
                    connection["error"] = (
                        "Slack installation is missing required Investigate scopes."
                    )
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
        "last_operation": state.last_operation,
    }


async def update_settings(
    policy: dict[str, Any], expected_version: int, actor: dict[str, Any]
) -> dict[str, str]:
    current = await get_policy()
    if expected_version != current.version:
        raise HTTPException(409, "Settings changed; reload before saving")
    try:
        candidate = InvestigationPolicy.model_validate(policy)
    except ValidationError as exc:
        raise HTTPException(
            422,
            "Invalid investigation policy: "
            + "; ".join(error["msg"] for error in exc.errors(include_input=False)),
        ) from exc
    # Workspace and app identity are server-owned: the bot token's workspace and
    # SLACK_APP_ID. Client-supplied values are ignored.
    candidate.workspace_id = current.workspace_id
    candidate.slack_app_id = current.slack_app_id
    if candidate.model:
        from agent.dashboard.options import SUPPORTED_MODEL_IDS

        if candidate.model not in SUPPORTED_MODEL_IDS:
            raise HTTPException(422, "Unsupported investigation model")
    if candidate.enabled:
        app_id = ENV.SLACK_APP_ID.get()
        if not app_id:
            raise HTTPException(
                422, "Set SLACK_APP_ID to the installed Slack app ID before enabling Investigate"
            )
        try:
            auth = await slack.request("auth.test")
        except Exception as exc:
            raise HTTPException(
                422, "Slack verification failed; check installation and permissions"
            ) from exc
        workspace_id = str(auth.get("team_id") or "")
        if not workspace_id:
            raise HTTPException(422, "Slack installation did not report a workspace")
        bound = (current.workspace_id, current.slack_app_id)
        identity = (workspace_id, app_id)
        if bound != identity and any(bound) and await INVESTIGATIONS.search(limit=1):
            raise HTTPException(
                409, "Slack workspace or app cannot change with registered investigations"
            )
        candidate.workspace_id, candidate.slack_app_id = identity
        await ensure_recovery()
    candidate.version = current.version + 1
    candidate.enabled_at = (
        time.time() if candidate.enabled and not current.enabled else current.enabled_at
    )
    receipt_id = str(uuid4())
    await RECEIPTS.put(
        receipt_id,
        Receipt(
            id=receipt_id,
            workspace_id=candidate.workspace_id,
            kind="settings",
            payload={"policy": candidate.model_dump(), "expected_version": expected_version},
            actor=actor,
            received_at=time.time(),
        ),
    )
    await wake()
    return {"command_id": receipt_id, "status": "accepted"}


def summary(record: Investigation) -> dict[str, Any]:
    return {
        "id": record.id,
        "channel_id": record.channel_id,
        "channel_name": record.channel_name,
        "title": record.title or record.channel_name,
        "is_archived": record.is_archived,
        "status": record.status,
        "reason": record.reason,
        "latest_finding": record.report.summary if record.report else "",
        "updated_at": record.updated_at,
        "slack_url": f"https://slack.com/archives/{record.channel_id}",
    }


async def readable(record: Investigation, policy: InvestigationPolicy) -> bool:
    if record.expired or record.reason == "code_channel":
        return False
    if record.last_verified_at > time.time() - 60:
        return record.can_read
    try:
        info = await slack.channel_info(record.channel_id)
    except Exception:
        return False
    return slack.channel_allowed(info, policy, for_read=True)


async def list_investigations(
    view: str | None = None,
    q: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
    *,
    include_setup: bool = False,
) -> dict[str, Any]:
    policy = await get_policy()
    records = sorted(await INVESTIGATIONS.search_all(), key=lambda r: r.updated_at, reverse=True)
    items = []
    for record in records:
        if record.workspace_id != policy.workspace_id or record.expired:
            continue
        can_read = await readable(record, policy)
        if not can_read and not (include_setup and not record.joined):
            continue
        item = summary(record) if can_read else setup_summary(record)
        if view and view != "all":
            if view == "active" and record.status not in {"pending", "watching", "investigating"}:
                continue
            if view != "active" and record.status != view:
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


def setup_summary(record: Investigation) -> dict[str, Any]:
    return {
        "id": record.id,
        "channel_id": record.channel_id,
        "channel_name": "",
        "title": "Channel setup needs attention"
        if record.status != "pending"
        else "Joining channel",
        "is_archived": False,
        "status": record.status,
        "reason": record.reason,
        "latest_finding": "",
        "updated_at": record.updated_at,
        "slack_url": "",
    }


async def get_investigation(id: str, *, include_setup: bool = False) -> dict[str, Any]:
    record = await INVESTIGATIONS.get(id)
    policy = await get_policy()
    if not record or record.expired or record.workspace_id != policy.workspace_id:
        raise HTTPException(404, "Investigation not found")
    if not await readable(record, policy):
        if include_setup and not record.joined:
            return {
                "investigation": setup_summary(record),
                "report": None,
                "coverage": {
                    "gaps": [
                        "Channel setup has not established readable public-channel membership. Check Slack grants and configuration."
                    ]
                },
                "activity": [],
                "allowed_actions": [],
                "trace_url": None,
                "next_cursor": None,
            }
        raise HTTPException(404, "Investigation not found")
    actions = ["ask", "investigate_again"]
    if record.status == "completed":
        if not record.is_archived:
            actions.append("reopen")
    elif record.status == "paused":
        actions.extend(["resume", "complete"])
    else:
        actions.extend(["pause", "complete"])
    return {
        "investigation": summary(record),
        "report": record.report.model_dump() if record.report else None,
        "coverage": {"gaps": record.gaps},
        "activity": [item.model_dump() for item in reversed(record.activity[-100:])],
        "allowed_actions": actions,
        "trace_url": await get_langsmith_trace_url(record.thread_id),
        "next_cursor": None,
    }


async def submit_command(
    id: str, action: str, text: str | None, request_id: str, actor: dict[str, Any]
) -> dict[str, str]:
    detail = await get_investigation(id)
    if action == "ask" and (not text or not text.strip() or len(text) > 8000):
        raise HTTPException(422, "A question of 1–8000 characters is required")
    record = await INVESTIGATIONS.get(id)
    assert record is not None
    receipt_id = fingerprint([id, actor.get("id") or actor.get("github_login"), request_id])
    payload = {"action": action, "text": text or ""}
    existing = await RECEIPTS.get(receipt_id)
    if existing:
        if existing.content_hash != fingerprint(payload):
            raise HTTPException(409, "Request ID was reused with different content")
        return {"command_id": receipt_id, "status": "duplicate"}
    if action not in detail["allowed_actions"]:
        raise HTTPException(409, "Action is not available in this investigation state")
    await RECEIPTS.put(
        receipt_id,
        Receipt(
            id=receipt_id,
            workspace_id=record.workspace_id,
            channel_id=record.channel_id,
            kind="command",
            payload=redact_context(payload),
            actor=actor,
            received_at=time.time(),
            content_hash=fingerprint(payload),
        ),
    )
    await wake()
    return {"command_id": receipt_id, "status": "accepted"}


async def recover() -> dict[str, str]:
    await _wake()
    return {"status": "accepted"}
