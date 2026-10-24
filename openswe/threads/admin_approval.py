"""One-use, owner-authorized approvals for shared-channel writes and permitted disclosures."""

import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import langgraph_sdk
from pydantic import BaseModel
from sqlalchemy import select, text
from uuid_utils import uuid7

from openswe.database.postgres import transaction
from openswe.run_config import RunConfig
from openswe.slack.channels import SlackChannel
from openswe.slack.client import post_slack_ephemeral_message
from openswe.source_context import SlackThreadRef, SourceContext
from openswe.tools.admin_gate import participant_is_admin
from openswe.users import User
from openswe.users.records import UnknownUser, user_id_for_login
from openswe.utils.json_types import thread_metadata


class Approval(BaseModel):
    request_id: str
    owner: str
    channel_id: str
    thread_ts: str
    fingerprint: str
    expires_at: datetime
    status: Literal["pending", "approved", "rejected", "consumed"] = "pending"


class AdminApprovals:
    async def get(self, thread_id: str) -> Approval | None:
        async with transaction() as conn:
            result = await conn.execute(
                text("""
                    SELECT a.approval, i.login AS owner
                    FROM admin_write_approval a
                    JOIN LATERAL (
                        SELECT login FROM user_identity
                        WHERE user_id = a.owner_id AND provider = 'github'
                        ORDER BY last_seen_at DESC LIMIT 1
                    ) i ON true
                    WHERE a.thread_id = :thread_id
                """),
                {"thread_id": thread_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            payload = row["approval"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            return Approval.model_validate({**payload, "owner": row["owner"].lower()})

    async def put(self, thread_id: str, record: Approval) -> None:
        async with transaction() as conn:
            owner_id = await conn.scalar(select(user_id_for_login(record.owner)))
            if owner_id is None:
                raise UnknownUser(record.owner)
            await conn.execute(
                text("""
                    INSERT INTO admin_write_approval
                        (id, thread_id, owner_id, approval, created_by, updated_by)
                    VALUES (:id, :thread_id, :owner_id, CAST(:approval AS jsonb), :owner_id, :owner_id)
                    ON CONFLICT (thread_id) DO UPDATE SET
                        owner_id = EXCLUDED.owner_id, approval = EXCLUDED.approval,
                        updated_at = clock_timestamp(), updated_by = EXCLUDED.updated_by
                """),
                {
                    "id": uuid7(),
                    "thread_id": thread_id,
                    "owner_id": owner_id,
                    "approval": record.model_dump_json(exclude={"owner"}),
                },
            )


APPROVALS = AdminApprovals()


@asynccontextmanager
async def _lock(thread_id: str) -> AsyncIterator[None]:
    async with transaction() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
            {"subject": f"admin-approval:{thread_id}"},
        )
        yield


async def _owner(thread_id: str) -> tuple[str, SlackThreadRef] | None:
    metadata = thread_metadata(await langgraph_sdk.get_client().threads.get(thread_id))
    owner = metadata.get("owner_login")
    context = SourceContext.from_metadata(metadata)
    slack = context.slack_thread
    if (
        metadata.get("owner_type") != "user"
        or metadata.get("visibility", "public") != "public"
        or not isinstance(owner, str)
        or not owner
        or context.github_issue is not None
        or context.linear_issue is not None
        or slack is None
        or slack.location is None
    ):
        return None
    if not await participant_is_admin(owner):
        return None
    channel = await SlackChannel.context_for(slack.channel_id, use_cache=False)
    if channel.is_im is not False or channel.is_mpim is not False or not channel.allows_operations:
        return None
    return owner.lower(), slack


async def approval_owner(cfg: RunConfig) -> tuple[str, SlackThreadRef] | None:
    """Only the saved owner's directly initiated channel run may request or execute."""
    if (
        not cfg.thread_id
        or cfg.source != "slack"
        or cfg.background_task_completion
        or cfg.schedule_id
        or cfg.watch_key
        or not cfg.github_login
        or cfg.slack_thread is None
    ):
        return None
    identity = await _owner(cfg.thread_id)
    if identity is None:
        return None
    owner, slack = identity
    actor = await User.login_for_slack(cfg.slack_thread.triggering_user_id)
    if (
        cfg.github_login.lower() != owner
        or not actor
        or actor.lower() != owner
        or cfg.slack_thread.location != slack.location
    ):
        return None
    return identity


def _live(record: Approval, owner: str, slack: SlackThreadRef) -> bool:
    return (
        record.owner == owner
        and (record.channel_id, record.thread_ts) == slack.location
        and record.expires_at > datetime.now(UTC)
    )


async def authorize_admin_write(
    cfg: RunConfig,
    tool: str,
    arguments: Mapping[str, object],
    *,
    disclosure: bool = False,
) -> dict[str, object] | None:
    """Consume the exact approved call, or block it and request owner approval."""
    if not cfg.thread_id:
        return {"ok": False, "error": "Missing thread identity."}
    serialized = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(serialized) > 12000:
        return {
            "ok": False,
            "error": "This action is too large to review in Slack; use a private thread.",
        }
    async with _lock(cfg.thread_id):
        identity = await approval_owner(cfg)
        if identity is None:
            return {
                "ok": False,
                "error": "Only this thread's currently authorized admin owner can request this action.",
            }
        owner, slack = identity
        fingerprint = hashlib.sha256(
            json.dumps(
                [cfg.thread_id, owner, slack.location, tool, serialized, disclosure]
            ).encode()
        ).hexdigest()
        record = await APPROVALS.get(cfg.thread_id)
        if record and _live(record, owner, slack) and record.fingerprint == fingerprint:
            if record.status == "approved":
                record.status = "consumed"
                await APPROVALS.put(cfg.thread_id, record)
                return None
            if record.status in {"pending", "rejected"}:
                return {"ok": False, "status": f"approval_{record.status}"}
        record = Approval(
            request_id=str(uuid4()),
            owner=owner,
            channel_id=slack.channel_id,
            thread_ts=slack.thread_ts,
            fingerprint=fingerprint,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        await APPROVALS.put(cfg.thread_id, record)
        assert cfg.slack_thread is not None
        notice = (
            f"Run this read and share its permitted results in channel {slack.channel_id}, "
            f"thread {slack.thread_ts}? Everyone with access to the thread can see the results."
            if disclosure
            else "Review this admin action."
        )
        sent = await post_slack_ephemeral_message(
            slack.channel_id,
            cfg.slack_thread.triggering_user_id,
            f"{notice} Approval expires in 15 minutes and authorizes one attempt.",
            thread_ts=slack.thread_ts,
            blocks=_blocks(
                tool,
                serialized,
                record.request_id,
                disclosure_notice=notice if disclosure else None,
            ),
        )
        if not sent:
            record.status = "rejected"
            await APPROVALS.put(cfg.thread_id, record)
            return {
                "ok": False,
                "error": "Could not deliver owner approval; no action was taken. Use a private thread.",
            }
    return {"ok": False, "status": "approval_pending"}


def _blocks(
    tool: str, arguments: str, request_id: str, *, disclosure_notice: str | None = None
) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = [
        {
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": f"Approve {tool}? One attempt; expires in 15 minutes.",
            },
        },
    ]
    if disclosure_notice:
        blocks.append(
            {"type": "section", "text": {"type": "plain_text", "text": disclosure_notice}}
        )
    blocks.extend(
        {"type": "section", "text": {"type": "plain_text", "text": arguments[start : start + 2900]}}
        for start in range(0, len(arguments), 2900)
    )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": label},
                    "action_id": f"open_swe_option_select_admin_{action}",
                    "value": json.dumps(
                        {
                            "type": "admin_write_approval",
                            "action": action,
                            "fingerprint": request_id,
                        }
                    ),
                }
                for action, label in (
                    (
                        "approve",
                        "Approve read and share" if disclosure_notice else "Approve exact action",
                    ),
                    ("reject", "Reject"),
                )
            ],
        }
    )
    return blocks


async def decide_admin_approval(
    thread_id: str,
    request_id: str,
    *,
    slack_user_id: str,
    channel_id: str,
    thread_ts: str,
    approved: bool,
) -> bool:
    """Accept a signed Slack decision only from the currently authorized owner."""
    async with _lock(thread_id):
        identity = await _owner(thread_id)
        actor = await User.login_for_slack(slack_user_id)
        if identity is None or not actor:
            return False
        owner, slack = identity
        if actor.lower() != owner or slack.location != (channel_id, thread_ts):
            return False
        record = await APPROVALS.get(thread_id)
        if (
            record is None
            or not _live(record, owner, slack)
            or record.request_id != request_id
            or record.status != "pending"
        ):
            return False
        record.status = "approved" if approved else "rejected"
        await APPROVALS.put(thread_id, record)
        return True
