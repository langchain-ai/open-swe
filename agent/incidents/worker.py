"""Serialized channel workers with durable request and publication checkpoints."""

import asyncio
import logging
import math
import re
import time
from typing import Any, Literal

from agent.config import ENV
from agent.dispatch import create_durable_run
from agent.incidents import documents, providers, service, slack
from agent.incidents.access import is_observability_authorized
from agent.incidents.models import (
    Activity,
    Incident,
    IncidentMessage,
    IncidentPolicy,
    IncidentReport,
    PendingPublication,
    PendingRequest,
    Publication,
    Receipt,
)
from agent.incidents.presentation import report_message
from agent.store import now_iso
from agent.utils.dashboard_links import dashboard_incident_url

logger = logging.getLogger(__name__)
_STOPPED_STATES = {"paused", "completed"}
_MAX_PENDING_REQUESTS = 100
_MESSAGE_QUIET_SECONDS = 15
_MAX_MESSAGE_WAIT_SECONDS = 60


class IncidentStopped(RuntimeError):
    """A checked control or access change stopped the current pass."""


async def run_engine(*args: Any, **kwargs: Any) -> IncidentReport:
    from agent.incidents.engine import incidents

    return await incidents(*args, **kwargs)


async def schedule_wake(seconds: float = 0) -> None:
    thread_id = service.incident_id("coordinator", "default")
    await create_durable_run(
        thread_id,
        "incidents_coordinator",
        input={},
        source="incidents_coordinator",
        metadata={"source": "incidents_coordinator"},
        multitask_strategy="enqueue",
        after_seconds=max(0, seconds),
    )


def note(record: Incident, kind: str, text: str) -> None:
    if not record.activity or record.activity[-1].summary != text:
        record.activity.append(Activity(type=kind, summary=text))
        record.activity = record.activity[-100:]


async def save(record: Incident) -> None:
    record.updated_at = now_iso()
    await service.INVESTIGATIONS.put(record.id, record)
    await documents.preserve_metadata(record)


async def channel_receipts(record: Incident) -> list[Receipt]:
    receipts = await service.RECEIPTS.search_all(filter={"channel_id": record.channel_id})
    return sorted(
        [
            r
            for r in receipts
            if r.workspace_id == record.workspace_id
            and r.id not in record.processed_receipts
            and r.available_at <= time.time()
        ],
        key=lambda r: (receipt_time(r), r.received_at, r.id),
    )


def receipt_time(receipt: Receipt) -> float:
    if receipt.kind in {"app_mention", "agent_session_stopped"}:
        try:
            timestamp = float(
                receipt.payload.get("event_ts") or receipt.payload.get("ts") or receipt.source_time
            )
            if math.isfinite(timestamp) and timestamp > 0:
                return timestamp
        except TypeError, ValueError:
            pass
    return receipt.received_at


def control_order(receipt: Receipt, action: str) -> tuple[float, int]:
    return receipt_time(receipt), 2 if action == "complete" else 1 if action == "pause" else 0


def stale_control(record: Incident, receipt: Receipt, action: str) -> bool:
    return control_order(receipt, action) < (record.last_control_at, record.last_control_priority)


def remember_control(record: Incident, receipt: Receipt, action: str) -> None:
    record.last_control_at, record.last_control_priority = control_order(receipt, action)


async def command_for_receipt(
    receipt: Receipt, policy: IncidentPolicy, record: Incident | None = None
) -> tuple[str, str]:
    if receipt.kind == "command":
        return str(receipt.payload.get("action") or ""), str(receipt.payload.get("text") or "")
    if receipt.kind == "agent_session_stopped":
        if (
            record is None
            or not receipt.payload.get("thread_ts")
            or receipt.payload["thread_ts"]
            not in {record.anchor_ts, record.slack_session_thread_ts}
            or not receipt.payload.get("user")
            or receipt.payload.get("bot_id")
        ):
            return "", ""
        info = await slack.request("users.info", user=receipt.payload["user"])
        email = (info.get("user", {}).get("profile") or {}).get("email")
        return ("pause", "") if is_observability_authorized(email) else ("", "")
    if receipt.kind != "app_mention":
        return "", ""
    raw = str(receipt.payload.get("text") or "").strip()
    match = re.fullmatch(r"<@[A-Z0-9]+>\s+(.*)", raw, flags=re.DOTALL)
    user = receipt.payload.get("user")
    if not match or not user or receipt.payload.get("bot_id"):
        return "", ""
    info = await slack.request("users.info", user=user)
    email = (info.get("user", {}).get("profile") or {}).get("email")
    if not is_observability_authorized(email):
        return "", ""
    text = match.group(1).strip()
    return (
        (text.lower(), "")
        if text.lower() in {"pause", "resume", "complete", "reopen"}
        else ("ask", text[:8000])
    )


def _merge_message(record: Incident, message: IncidentMessage) -> bool:
    previous = next((m for m in record.messages if m.id == message.id), None)
    if previous == message:
        return False
    if previous and previous.deleted and not message.deleted:
        return False
    if previous and float(previous.edited_at or previous.ts or 0) > float(
        message.edited_at or message.ts or 0
    ):
        return False
    record.messages = [m for m in record.messages if m.id != message.id] + [message]
    record.messages = sorted(record.messages, key=lambda m: float(m.ts or 0))[-500:]
    return True


def debounce_delay(record: Incident, now: float) -> float:
    if not record.pending_since:
        return 0
    quiet_until = (record.pending_message_at or record.pending_since) + _MESSAGE_QUIET_SECONDS
    deadline = record.pending_since + _MAX_MESSAGE_WAIT_SECONDS
    return max(0, min(quiet_until, deadline) - now)


def _own_message(message: IncidentMessage, policy: IncidentPolicy) -> bool:
    bot_user = ENV.SLACK_BOT_USER_ID.get()
    return (
        bool(bot_user and message.user == bot_user)
        or bool(policy.slack_app_id and message.app_id == policy.slack_app_id)
        or message.event_type in {"incidents_report", "open_swe_incident"}
    )


def _code_channel(channel: dict[str, Any]) -> bool:
    properties = channel.get("properties")
    record = properties.get("record_channel") if isinstance(properties, dict) else None
    return isinstance(record, dict) and record.get("record_type") == "agent_channel"


def _restore_watch_state(record: Incident) -> None:
    if record.pass_return_status:
        record.status, record.reason = record.pass_return_status, record.pass_return_reason
        record.pass_return_status, record.pass_return_reason = None, ""


def _restrict(record: Incident, reason: str) -> None:
    _restore_watch_state(record)
    if record.status not in _STOPPED_STATES:
        record.status, record.reason = "paused", reason
    record.pending_publications = []


def _queue_publication(
    record: Incident,
    policy: IncidentPolicy,
    reason: str,
    text: str,
    *,
    key: str,
    thread_ts: str | None = None,
) -> None:
    if record.is_archived:
        return
    blocks: list[dict[str, Any]] = []
    if record.report and reason in {"findings", "answer", "completion"}:
        text, blocks = report_message(
            record.report, text, dashboard_incident_url(record.id), reason=reason
        )
    if not any(p.reason == reason and p.key == key for p in record.pending_publications):
        record.pending_publications.append(
            PendingPublication(
                reason=reason,
                text=text[:12000],
                blocks=blocks,
                key=key,
                thread_ts=thread_ts,
                policy_version=policy.version,
            )
        )


def _apply_stop(record: Incident, policy: IncidentPolicy, action: str, key: str) -> None:
    _restore_watch_state(record)
    if action == "complete" or record.status != "completed":
        record.status = "completed" if action == "complete" else "paused"
        record.reason = "responder_" + action
    record.pending_requests = []
    record.pending_publications = []
    record.pending_since = record.pending_message_at = 0
    record.slack_session_thread_ts = record.slack_session_thread_ts or record.anchor_ts
    record.retry_after = 0
    note(
        record,
        "control",
        "Incident completed." if action == "complete" else "Incident paused.",
    )
    if action == "complete" and record.report:
        _queue_publication(
            record,
            policy,
            "completion",
            "Incident complete. " + record.report.summary,
            key=key,
        )
    else:
        _queue_publication(
            record,
            policy,
            "control",
            "Incident complete."
            if action == "complete"
            else "Incident paused. Mention me with `resume` to continue.",
            key=key,
        )


async def _apply_pending_stops(record: Incident, policy: IncidentPolicy) -> bool:
    receipts = await channel_receipts(record)
    stops: dict[str, str] = {}
    last_stop = -1
    for index, receipt in enumerate(receipts):
        if receipt.kind == "command":
            if receipt.payload.get("action") not in {"pause", "complete"}:
                continue
        elif receipt.kind == "app_mention":
            if not re.fullmatch(
                r"<@[A-Z0-9]+>\s+(?:pause|complete)",
                str(receipt.payload.get("text") or "").strip(),
                flags=re.IGNORECASE,
            ):
                continue
        elif receipt.kind != "agent_session_stopped":
            continue
        action, _ = await command_for_receipt(receipt, policy, record)
        if action not in {"pause", "complete"} or stale_control(record, receipt, action):
            continue
        stops[receipt.id] = action
        last_stop = index
    if last_stop < 0:
        return False
    for receipt in receipts[: last_stop + 1]:
        action = stops.get(receipt.id)
        if action and not stale_control(record, receipt, action):
            remember_control(record, receipt, action)
            _apply_stop(record, policy, action, receipt.id)
        else:
            action, _ = await command_for_receipt(receipt, policy, record)
        if action:
            record.processed_receipts.append(receipt.id)
    await save(record)
    return True


async def _guard(
    record: Incident, policy: IncidentPolicy, *, explicit: bool, publishing: bool = False
) -> None:
    if await _apply_pending_stops(record, policy):
        raise IncidentStopped("Incident stop requested")
    current = await service.get_policy()
    if not current.enabled or current.version != policy.version:
        _restrict(record, "disabled" if not current.enabled else "policy_changed")
        await save(record)
        raise IncidentStopped("Incident policy changed")
    try:
        channel = await slack.channel_info(record.channel_id)
    except Exception:
        record.can_read, record.last_verified_at = False, 0
        await save(record)
        raise
    if _code_channel(channel):
        record.can_read = False
        _restrict(record, "code_channel")
        record.reason = "code_channel"
        record.pending_requests = []
        await save(record)
        raise IncidentStopped("Coding channel is not eligible for Incidents")
    if not slack.channel_allowed(channel, current, for_read=True):
        record.can_read = False
        _restrict(record, "access_restricted")
        await save(record)
        raise IncidentStopped("Incident access revoked")
    if (publishing or not explicit) and not slack.channel_allowed(channel, current):
        if channel.get("is_archived"):
            record.status, record.reason = "completed", "channel_archived"
            record.pass_return_status = None
            record.pending_publications = []
        else:
            _restrict(record, "channel_ineligible")
        await save(record)
        raise IncidentStopped("Incident channel no longer active")


def _introduction_text(record: Incident, policy: IncidentPolicy) -> str:
    text = "Incidents is following this channel. Findings will appear in this channel"
    link = dashboard_incident_url(record.id)
    if link:
        text += f"; the full incident is at <{link}|Open incident>"
    return text + ". Mention me with a question."


async def _publish(
    record: Incident,
    policy: IncidentPolicy,
    reason: str,
    text: str,
    *,
    thread_ts: str | None = None,
    key: str = "",
    blocks: list[dict[str, Any]] | None = None,
) -> None:
    if record.is_archived:
        return
    explicit = reason in {"answer", "completion", "control"}
    await _guard(record, policy, explicit=explicit, publishing=True)
    publication_id = service.fingerprint([record.id, reason, key or text, thread_ts])
    publication = await service.PUBLICATIONS.get(publication_id)
    if publication and publication.status in {"sent", "unknown", "sending", "failed"}:
        if publication.status == "sending":
            publication.status = "unknown"
            await service.PUBLICATIONS.put(publication.id, publication)
            note(
                record,
                "delivery",
                "Slack delivery is uncertain; check the channel before retrying.",
            )
        if reason == "introduction" and publication.slack_message_ts:
            record.anchor_ts = publication.slack_message_ts
        return
    publication = publication or Publication(
        id=publication_id,
        incident_id=record.id,
        text=text[:12000],
        blocks=blocks or [],
        thread_ts=thread_ts,
        reason=reason,
        created_at=time.time(),
    )
    await service.PUBLICATIONS.put(publication.id, publication)
    publication.status = "sending"
    await service.PUBLICATIONS.put(publication.id, publication)
    try:
        await _guard(record, policy, explicit=explicit, publishing=True)
    except IncidentStopped:
        publication.status = "failed"
        await service.PUBLICATIONS.put(publication.id, publication)
        raise
    except Exception:
        publication.status = "pending"
        await service.PUBLICATIONS.put(publication.id, publication)
        raise
    try:
        publication.slack_message_ts = await slack.publish(
            record.channel_id,
            publication.text,
            publication.thread_ts,
            publication.id,
            blocks=publication.blocks,
        )
        publication.status = "sent"
        record.last_published_at = time.time()
        if reason == "introduction":
            record.anchor_ts = publication.slack_message_ts
    except Exception:
        publication.status = "unknown"
        note(record, "delivery", "Slack delivery is uncertain; the report remains available here.")
        logger.warning("Incident Slack delivery uncertain", extra={"incident_id": record.id})
    await service.PUBLICATIONS.put(publication.id, publication)


async def _flush_publications(record: Incident, policy: IncidentPolicy) -> None:
    while record.pending_publications:
        pending = record.pending_publications[0]
        if pending.policy_version == policy.version:
            await _publish(
                record,
                policy,
                pending.reason,
                pending.text,
                thread_ts=pending.thread_ts,
                key=pending.key,
                blocks=pending.blocks,
            )
        record.pending_publications.pop(0)
        await save(record)


async def _consume_receipts(record: Incident, policy: IncidentPolicy, info: dict[str, Any]) -> None:
    for receipt in await channel_receipts(record):
        if receipt.kind == "agent_followup":
            from agent.incidents.followups import followup_current

            if await followup_current(receipt, record, policy):
                _merge_message(
                    record,
                    IncidentMessage(
                        id=receipt.id,
                        ts=str(receipt.available_at),
                        event_type="agent_followup",
                        text="System followup (context only; no new action authorization):\n"
                        + str(receipt.payload.get("text") or ""),
                    ),
                )
            record.processed_receipts.append(receipt.id)
            continue
        if await documents.process_document_receipt(
            record, receipt
        ) or await providers.process_provider_receipt(record, receipt):
            record.processed_receipts.append(receipt.id)
            await save(record)
            continue
        if receipt.kind in {"message", "app_mention"}:
            data = receipt.payload
            normalized = slack.message(record.channel_id, data.get("message", data))
            if _own_message(normalized, policy):
                record.processed_receipts.append(receipt.id)
                continue
        action, text = await command_for_receipt(receipt, policy, record)
        if action and stale_control(record, receipt, action):
            action = ""
        if action in {"pause", "complete"}:
            remember_control(record, receipt, action)
            _apply_stop(record, policy, action, receipt.id)
        elif (action == "resume" and record.status == "paused") or (
            action == "reopen" and record.status == "completed"
        ):
            if not record.is_archived and slack.channel_allowed(info, policy):
                remember_control(record, receipt, action)
                record.status, record.reason = "pending", ""
                record.slack_session_thread_ts = record.anchor_ts
                record.watch_started_at, record.retry_after = time.time(), 0
                note(record, "control", "Incident watching resumed.")
                _queue_publication(
                    record,
                    policy,
                    "control",
                    "Incident resumed.",
                    key=receipt.id,
                )
        elif action in {"ask", "investigate_again"}:
            if len(record.pending_requests) >= _MAX_PENDING_REQUESTS:
                continue
            if not any(request.id == receipt.id for request in record.pending_requests):
                record.pending_requests.append(
                    PendingRequest(
                        id=receipt.id,
                        text=text,
                        thread_ts=str(receipt.payload.get("thread_ts") or "") or None,
                    )
                )
                record.retry_after = 0
                note(record, "question", text or "Another incident pass requested.")
        if receipt.kind in {"message", "app_mention"}:
            data = receipt.payload
            subtype = data.get("subtype")
            changed = False
            if subtype == "message_deleted":
                msg = slack.message(
                    record.channel_id,
                    {"ts": data.get("deleted_ts"), "edited": {"ts": data.get("event_ts")}},
                )
                msg.deleted = True
                changed = _merge_message(record, msg)
                record.report = None
                record.reset_conversation = True
                record.last_context_hash = ""
                record.pending_publications = []
                note(record, "evidence", "A Slack message was deleted; findings will be rechecked.")
            else:
                if subtype == "message_changed":
                    record.reset_conversation = True
                msg = slack.message(
                    record.channel_id,
                    data.get("message", data) if subtype == "message_changed" else data,
                )
                if msg.ts:
                    changed = _merge_message(record, msg)
                    if subtype != "message_changed":
                        record.last_source_activity_at = max(
                            record.last_source_activity_at, float(msg.ts or 0)
                        )
            if changed and (msg.deleted or msg.text.strip()) and not action:
                arrived = min(receipt.received_at, time.time())
                record.pending_since = min(record.pending_since or arrived, arrived)
                record.pending_message_at = max(record.pending_message_at, arrived)
        record.processed_receipts.append(receipt.id)
    await save(record)


def _failure(record: Incident, reason: str, *, retry_after: float = 60) -> None:
    _restore_watch_state(record)
    if record.status not in _STOPPED_STATES:
        record.status, record.reason = "needs_attention", reason
    record.retry_after = time.time() + max(60, retry_after)
    note(
        record,
        "error",
        "The incident could not finish. Check source access and model configuration, then retry.",
    )


async def _session_status(
    record: Incident, status: Literal["processing", "active", "suspended", "closed"]
) -> bool:
    if not record.slack_session_thread_ts or not record.can_read:
        return True
    try:
        await slack.set_session_status(
            record.channel_id, record.slack_session_thread_ts, status, record.title
        )
        return True
    except Exception:
        logger.warning(
            "Incident Slack session status unavailable",
            extra={"incident_id": record.id, "session_status": status},
        )
        return False


async def process_channel(incident_id: str) -> dict[str, Any]:
    try:
        return await _process_channel(incident_id)
    finally:
        record = await service.INVESTIGATIONS.get(incident_id)
        if record and record.slack_session_thread_ts:
            status = (
                "closed"
                if record.status == "completed"
                else "active"
                if record.status in {"pending", "watching"}
                else "suspended"
            )
            if await _session_status(record, status):
                record.slack_session_thread_ts = None
                await save(record)


async def _process_channel(incident_id: str) -> dict[str, Any]:
    record = await service.INVESTIGATIONS.get(incident_id)
    if not record:
        return {"status": "ignored"}
    _restore_watch_state(record)
    policy = await service.get_policy()
    if record.expired or not policy.enabled:
        for receipt in await channel_receipts(record):
            if await documents.process_document_receipt(
                record, receipt
            ) or await providers.process_provider_receipt(record, receipt):
                record.processed_receipts.append(receipt.id)
        await save(record)
        if record.expired:
            return {"status": "completed"}
    if not policy.enabled:
        _restrict(record, "disabled")
        await save(record)
        return {"status": record.status}
    try:
        info = await slack.channel_info(record.channel_id)
        if info.get("id") != record.channel_id:
            raise slack.SlackError("channel_identity_mismatch")
        if _code_channel(info):
            record.status, record.reason, record.can_read = "paused", "code_channel", False
            record.pending_requests, record.pending_publications = [], []
            record.processed_receipts.extend(
                receipt.id for receipt in await channel_receipts(record)
            )
            await save(record)
            return {"status": record.status}
        if record.joined and info.get("is_member") is not True:
            record.can_read = False
            _restrict(record, "membership_lost")
            await save(record)
            return {"status": record.status}
        if not record.joined and not info.get("is_archived"):
            if not slack.channel_allowed(info, policy):
                record.can_read = False
                _restrict(record, "channel_ineligible")
                await save(record)
                return {"status": record.status}
            if info.get("is_member") is not True:
                await slack.join(record.channel_id)
                info = await slack.channel_info(record.channel_id)
            record.joined = info.get("is_member") is True
        record.can_read = slack.channel_allowed(info, policy, for_read=True)
        record.last_verified_at, record.is_archived = time.time(), bool(info.get("is_archived"))
        if not record.can_read:
            _restrict(record, "access_restricted")
            await save(record)
            return {"status": record.status}
        record.channel_name = str(info.get("name") or record.channel_name)
        record.title = record.channel_name
        await _consume_receipts(record, policy, info)
    except Exception as exc:
        record.can_read, record.last_verified_at = False, 0
        try:
            await _apply_pending_stops(record, policy)
        except Exception:
            logger.warning(
                "Incident control verification unavailable",
                extra={"incident_id": record.id},
            )
        record.setup_attempts += 1
        _failure(record, "slack_unavailable", retry_after=getattr(exc, "retry_after", 60))
        await save(record)
        return {"status": record.status}

    if record.is_archived:
        record.status, record.reason = "completed", "channel_archived"
    elif not slack.channel_allowed(info, policy):
        _restrict(record, "channel_ineligible")
    now = time.time()
    record.watch_started_at = record.watch_started_at or now
    if record.status not in _STOPPED_STATES:
        if now - record.watch_started_at >= policy.max_watch_seconds:
            _restrict(record, "watch_limit")
        elif (
            now - max(record.watch_started_at, record.last_source_activity_at)
            >= policy.idle_timeout_seconds
        ):
            _restrict(record, "idle")
    await save(record)
    if record.retry_after > now:
        return {"status": record.status}
    try:
        await _flush_publications(record, policy)
        if record.status in _STOPPED_STATES and not record.pending_requests:
            return {"status": record.status}
        request = record.pending_requests[0] if record.pending_requests else None
        explicit = request is not None
        return_status: Literal["watching", "paused", "completed"] = (
            "completed"
            if record.status == "completed"
            else "paused"
            if record.status == "paused"
            else "watching"
        )
        return_reason = record.reason if record.status in _STOPPED_STATES else ""
        if not record.bootstrap_complete:
            try:
                history, gaps = await slack.history(record.channel_id, policy)
                record.gaps = list(dict.fromkeys(record.gaps + gaps))[-50:]
                for message in history:
                    if not _own_message(message, policy):
                        _merge_message(record, message)
                for name in ("topic", "purpose"):
                    value = info.get(name)
                    value = value.get("value") if isinstance(value, dict) else value
                    if isinstance(value, str) and value.strip():
                        _merge_message(
                            record,
                            IncidentMessage(
                                id=name,
                                ts="0",
                                text=service.redact_context(value)[:4000],
                                source_url=f"https://slack.com/archives/{record.channel_id}",
                            ),
                        )
                record.bootstrap_complete = True
            except Exception:
                record.gaps.append("Channel history could not be read.")
        record.messages = [
            message for message in record.messages if not _own_message(message, policy)
        ]
        if not record.anchor_ts:
            await _publish(record, policy, "introduction", _introduction_text(record, policy))
        context = [m.model_dump() for m in record.messages if not m.deleted and m.text.strip()]
        context_hash = service.fingerprint(context)
        if not context and not request:
            record.status, record.reason = return_status, return_reason or "awaiting_context"
            record.pending_since = record.pending_message_at = 0
            await save(record)
            return {"status": record.status}
        if context_hash == record.last_context_hash and not explicit:
            record.status, record.reason = return_status, return_reason
            record.pending_since = record.pending_message_at = 0
            await save(record)
            return {"status": record.status}
        if not explicit and (record.report or record.pending_since):
            record.pending_since = record.pending_since or now
            delay = debounce_delay(record, time.time())
            if delay:
                await save(record)
                await schedule_wake(delay)
                return {"status": "debouncing"}
        record.pass_return_status, record.pass_return_reason = return_status, return_reason
        record.status, record.reason, record.pending_since = "investigating", "", 0
        record.pending_message_at = 0
        record.slack_session_thread_ts = (
            request.thread_ts if request else None
        ) or record.anchor_ts
        await save(record)

        async def before_tool_call() -> None:
            await _guard(record, policy, explicit=explicit)

        await before_tool_call()
        await _session_status(record, "processing")
        had_report = record.report is not None
        await providers.refresh_provider(record)
        document = await documents.document_context(record.id)
        expected_revision = (document.get("postmortem") or {}).get("revision", 0)
        report = await asyncio.wait_for(
            run_engine(
                record.messages,
                policy,
                record.report,
                request.text if request else None,
                before_tool_call=before_tool_call,
                incident_id=record.id,
                explicit=explicit,
            ),
            timeout=policy.max_pass_seconds,
        )
        await before_tool_call()
        current_record = await service.INVESTIGATIONS.get(record.id)
        if current_record:
            record.agent_thread_id = current_record.agent_thread_id
            record.provider_scope = current_record.provider_scope
            record.evidence_scope = current_record.evidence_scope
            record.messages = current_record.messages
        await documents.update_from_report(
            record, report, expected_revision=expected_revision, run_id=report.id
        )
        record.active_pass_id = None
        record.reset_conversation = False
        record.report = report
        record.gaps = list(dict.fromkeys(record.gaps + report.gaps))[-50:]
        record.last_context_hash, record.retry_after = context_hash, 0
        _restore_watch_state(record)
        if request:
            record.pending_requests = [
                queued for queued in record.pending_requests if queued.id != request.id
            ]
        note(record, "findings", report.summary)
        digest = service.fingerprint(report_message(report, report.summary, None)[0])
        if explicit or not had_report or digest != record.last_published_digest:
            _queue_publication(
                record,
                policy,
                "answer" if explicit else "findings",
                report.summary,
                thread_ts=request.thread_ts if request else None,
                key=request.id if request else digest,
            )
            record.last_published_digest = digest
        await save(record)
        await _flush_publications(record, policy)
    except IncidentStopped:
        _restore_watch_state(record)
    except Exception as exc:
        current_record = await service.INVESTIGATIONS.get(record.id)
        if current_record:
            record.agent_thread_id = current_record.agent_thread_id
            record.provider_scope = current_record.provider_scope
            record.evidence_scope = current_record.evidence_scope
            record.messages = current_record.messages
            record.active_pass_id = current_record.active_pass_id
        _failure(record, "incident_failed", retry_after=getattr(exc, "retry_after", 60))
        logger.warning("Incident pass failed", extra={"incident_id": record.id}, exc_info=True)
    await save(record)
    await service.wake()
    return {"status": record.status}
