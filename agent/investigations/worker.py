"""Serialized channel workers with durable request and publication checkpoints."""

import asyncio
import logging
import math
import re
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from agent.config import ENV
from agent.dashboard.admin import is_observability_authorized
from agent.dispatch import create_durable_run
from agent.investigations import service, slack
from agent.investigations.models import (
    Activity,
    Investigation,
    InvestigationMessage,
    InvestigationPolicy,
    InvestigationReport,
    PendingPublication,
    PendingRequest,
    Publication,
    Receipt,
)
from agent.store import now_iso
from agent.utils.dashboard_links import dashboard_investigation_url

logger = logging.getLogger(__name__)
_STOPPED_STATES = {"paused", "completed"}
_MAX_PENDING_REQUESTS = 100


class InvestigationStopped(RuntimeError):
    """A checked control or access change stopped the current pass."""


async def run_engine(*args: Any, **kwargs: Any) -> InvestigationReport:
    from agent.investigations.engine import investigate

    return await investigate(*args, **kwargs)


async def schedule_wake(seconds: float = 0) -> None:
    thread_id = service.investigation_id("coordinator", "default")
    await create_durable_run(
        thread_id,
        "investigate_coordinator",
        input={},
        source="investigate_coordinator",
        metadata={"source": "investigate_coordinator"},
        multitask_strategy="enqueue",
        after_seconds=max(0, seconds),
    )


def note(record: Investigation, kind: str, text: str) -> None:
    if not record.activity or record.activity[-1].summary != text:
        record.activity.append(Activity(type=kind, summary=text))
        record.activity = record.activity[-100:]


async def save(record: Investigation) -> None:
    record.updated_at = now_iso()
    await service.INVESTIGATIONS.put(record.id, record)


async def channel_receipts(record: Investigation) -> list[Receipt]:
    receipts = await service.RECEIPTS.search_all(filter={"channel_id": record.channel_id})
    return sorted(
        [
            r
            for r in receipts
            if r.workspace_id == record.workspace_id and r.id not in record.processed_receipts
        ],
        key=lambda r: (receipt_time(r), r.received_at, r.id),
    )


def receipt_time(receipt: Receipt) -> float:
    if receipt.kind == "app_mention":
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


def stale_control(record: Investigation, receipt: Receipt, action: str) -> bool:
    return control_order(receipt, action) < (record.last_control_at, record.last_control_priority)


def remember_control(record: Investigation, receipt: Receipt, action: str) -> None:
    record.last_control_at, record.last_control_priority = control_order(receipt, action)


async def _command(receipt: Receipt, policy: InvestigationPolicy) -> tuple[str, str]:
    if receipt.kind == "command":
        return str(receipt.payload.get("action") or ""), str(receipt.payload.get("text") or "")
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


def _merge_message(record: Investigation, message: InvestigationMessage) -> None:
    previous = next((m for m in record.messages if m.id == message.id), None)
    if previous and previous.deleted and not message.deleted:
        return
    if previous and float(previous.edited_at or previous.ts or 0) > float(
        message.edited_at or message.ts or 0
    ):
        return
    record.messages = [m for m in record.messages if m.id != message.id] + [message]
    record.messages = sorted(record.messages, key=lambda m: float(m.ts or 0))[-500:]


def _own_message(message: InvestigationMessage, policy: InvestigationPolicy) -> bool:
    bot_user = ENV.SLACK_BOT_USER_ID.get()
    return (
        bool(bot_user and message.user == bot_user)
        or bool(policy.slack_app_id and message.app_id == policy.slack_app_id)
        or message.event_type in {"investigate_report", "open_swe_investigation"}
    )


def _code_channel(channel: dict[str, Any]) -> bool:
    properties = channel.get("properties")
    record = properties.get("record_channel") if isinstance(properties, dict) else None
    return isinstance(record, dict) and record.get("record_type") == "agent_channel"


def _restore_watch_state(record: Investigation) -> None:
    if record.pass_return_status:
        record.status, record.reason = record.pass_return_status, record.pass_return_reason
        record.pass_return_status, record.pass_return_reason = None, ""


def _restrict(record: Investigation, reason: str) -> None:
    _restore_watch_state(record)
    if record.status not in _STOPPED_STATES:
        record.status, record.reason = "paused", reason
    record.pending_publications = []


def _queue_publication(
    record: Investigation,
    policy: InvestigationPolicy,
    reason: str,
    text: str,
    *,
    key: str,
    thread_ts: str | None = None,
) -> None:
    if record.is_archived:
        return
    if record.report and reason in {"findings", "answer", "completion"}:
        text = report_text(record.report, text)
    if not any(p.reason == reason and p.key == key for p in record.pending_publications):
        record.pending_publications.append(
            PendingPublication(
                reason=reason,
                text=text[:12000],
                key=key,
                thread_ts=thread_ts,
                policy_version=policy.version,
            )
        )


def report_text(report: InvestigationReport, text: str) -> str:
    """Slack digest of a report: lead text, then impact, hypotheses, questions, gaps, evidence."""
    sections = [text]
    if report.impact:
        sections.append(f"*Impact:* {report.impact}")
    if report.hypotheses:
        sections.append(
            "*Hypotheses:*\n"
            + "\n".join(f"• {h.title} ({h.assessment})" for h in report.hypotheses[:6])
        )
    if report.questions:
        sections.append("*Open questions:*\n" + "\n".join(f"• {q}" for q in report.questions[:5]))
    if report.gaps:
        sections.append("*Coverage gaps:*\n" + "\n".join(f"• {g}" for g in report.gaps[:5]))
    text = "\n\n".join(sections)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    links = []
    for index, evidence in enumerate(report.evidence[:20], start=1):
        try:
            url = urlsplit(evidence.url)
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.password
            ):
                continue
            if any(character.isspace() or character in "<>|" for character in evidence.url):
                continue
        except ValueError:
            continue
        link = f"<{evidence.url}|[{index}]>"
        text = text.replace(f"[{evidence.id}]", link)
        links.append(link)
    return text + ("\nEvidence: " + " ".join(links) if links else "")


def _apply_stop(record: Investigation, policy: InvestigationPolicy, action: str, key: str) -> None:
    _restore_watch_state(record)
    if action == "complete" or record.status != "completed":
        record.status = "completed" if action == "complete" else "paused"
        record.reason = "responder_" + action
    record.pending_requests = []
    record.pending_publications = []
    record.retry_after = 0
    note(
        record,
        "control",
        "Investigation completed." if action == "complete" else "Investigation paused.",
    )
    if action == "complete" and record.report:
        _queue_publication(
            record,
            policy,
            "completion",
            "Investigation complete. " + record.report.summary,
            key=key,
        )
    else:
        _queue_publication(
            record,
            policy,
            "control",
            "Investigation complete."
            if action == "complete"
            else "Investigation paused. Mention me with `resume` to continue.",
            key=key,
        )


async def _apply_pending_stops(record: Investigation, policy: InvestigationPolicy) -> bool:
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
        else:
            continue
        action, _ = await _command(receipt, policy)
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
            action, _ = await _command(receipt, policy)
        if action:
            record.processed_receipts.append(receipt.id)
    await save(record)
    return True


async def _guard(
    record: Investigation, policy: InvestigationPolicy, *, explicit: bool, publishing: bool = False
) -> None:
    if await _apply_pending_stops(record, policy):
        raise InvestigationStopped("Investigation stop requested")
    current = await service.get_policy()
    if not current.enabled or current.version != policy.version:
        _restrict(record, "disabled" if not current.enabled else "policy_changed")
        await save(record)
        raise InvestigationStopped("Investigation policy changed")
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
        raise InvestigationStopped("Coding channel is not eligible for Investigate")
    if not slack.channel_allowed(channel, current, for_read=True):
        record.can_read = False
        _restrict(record, "access_restricted")
        await save(record)
        raise InvestigationStopped("Investigation access revoked")
    if (publishing or not explicit) and not slack.channel_allowed(channel, current):
        if channel.get("is_archived"):
            record.status, record.reason = "completed", "channel_archived"
            record.pass_return_status = None
            record.pending_publications = []
        else:
            _restrict(record, "channel_ineligible")
        await save(record)
        raise InvestigationStopped("Investigation channel no longer active")


def _introduction_text(record: Investigation, policy: InvestigationPolicy) -> str:
    text = "Investigate is following this channel. Findings will appear here"
    link = dashboard_investigation_url(record.id)
    if link:
        text += f"; the full investigation is at <{link}|Open investigation>"
    return text + ". Mention me with a question."


async def _publish(
    record: Investigation,
    policy: InvestigationPolicy,
    reason: str,
    text: str,
    *,
    thread_ts: str | None = None,
    key: str = "",
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
        investigation_id=record.id,
        text=text[:12000],
        thread_ts=thread_ts,
        reason=reason,
        created_at=time.time(),
    )
    await service.PUBLICATIONS.put(publication.id, publication)
    publication.status = "sending"
    await service.PUBLICATIONS.put(publication.id, publication)
    try:
        await _guard(record, policy, explicit=explicit, publishing=True)
    except InvestigationStopped:
        publication.status = "failed"
        await service.PUBLICATIONS.put(publication.id, publication)
        raise
    except Exception:
        publication.status = "pending"
        await service.PUBLICATIONS.put(publication.id, publication)
        raise
    try:
        publication.slack_message_ts = await slack.publish(
            record.channel_id, publication.text, thread_ts, publication.id
        )
        publication.status = "sent"
        record.last_published_at = time.time()
        if reason == "introduction":
            record.anchor_ts = publication.slack_message_ts
    except Exception:
        publication.status = "unknown"
        note(record, "delivery", "Slack delivery is uncertain; the report remains available here.")
        logger.warning(
            "Investigation Slack delivery uncertain", extra={"investigation_id": record.id}
        )
    await service.PUBLICATIONS.put(publication.id, publication)


async def _flush_publications(record: Investigation, policy: InvestigationPolicy) -> None:
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
            )
        record.pending_publications.pop(0)
        await save(record)


async def _consume_receipts(
    record: Investigation, policy: InvestigationPolicy, info: dict[str, Any]
) -> None:
    for receipt in await channel_receipts(record):
        if receipt.kind in {"message", "app_mention"}:
            data = receipt.payload
            normalized = slack.message(record.channel_id, data.get("message", data))
            if _own_message(normalized, policy):
                record.processed_receipts.append(receipt.id)
                continue
        action, text = await _command(receipt, policy)
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
                record.watch_started_at, record.retry_after = time.time(), 0
                note(record, "control", "Investigation watching resumed.")
                _queue_publication(
                    record,
                    policy,
                    "control",
                    "Investigation resumed.",
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
                        # Answer in the channel unless the question itself came from a thread.
                        thread_ts=str(receipt.payload.get("thread_ts") or "") or None,
                    )
                )
                record.retry_after = 0
                note(record, "question", text or "Another investigation pass requested.")
        if receipt.kind in {"message", "app_mention"}:
            data = receipt.payload
            subtype = data.get("subtype")
            if subtype == "message_deleted":
                msg = slack.message(
                    record.channel_id,
                    {"ts": data.get("deleted_ts"), "edited": {"ts": data.get("event_ts")}},
                )
                msg.deleted = True
                _merge_message(record, msg)
                record.report = None
                record.last_context_hash = ""
                record.pending_publications = []
                note(record, "evidence", "A Slack message was deleted; findings will be rechecked.")
            else:
                msg = slack.message(
                    record.channel_id,
                    data.get("message", data) if subtype == "message_changed" else data,
                )
                if msg.ts:
                    _merge_message(record, msg)
                    if subtype != "message_changed":
                        record.last_source_activity_at = max(
                            record.last_source_activity_at, float(msg.ts or 0)
                        )
        record.processed_receipts.append(receipt.id)
    await save(record)


def _failure(record: Investigation, reason: str, *, retry_after: float = 60) -> None:
    _restore_watch_state(record)
    if record.status not in _STOPPED_STATES:
        record.status, record.reason = "needs_attention", reason
    record.retry_after = time.time() + max(60, retry_after)
    note(
        record,
        "error",
        "The investigation could not finish. Check source access and model configuration, then retry.",
    )


async def process_channel(investigation_id: str) -> dict[str, Any]:
    record = await service.INVESTIGATIONS.get(investigation_id)
    if not record or record.expired:
        return {"status": "ignored"}
    _restore_watch_state(record)
    policy = await service.get_policy()
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
                "Investigation control verification unavailable",
                extra={"investigation_id": record.id},
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
                            InvestigationMessage(
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
            await save(record)
            return {"status": record.status}
        if context_hash == record.last_context_hash and not explicit:
            record.status, record.reason = return_status, return_reason
            await save(record)
            return {"status": record.status}
        if record.report and not explicit:
            record.pending_since = record.pending_since or now
            if now - record.pending_since < 15:
                await save(record)
                await schedule_wake(15)
                return {"status": "debouncing"}
        record.pass_return_status, record.pass_return_reason = return_status, return_reason
        record.status, record.reason, record.pending_since = "investigating", "", 0
        await save(record)

        async def before_tool_call() -> None:
            await _guard(record, policy, explicit=explicit)

        await before_tool_call()
        had_report = record.report is not None
        report = await asyncio.wait_for(
            run_engine(
                record.messages,
                policy,
                record.report,
                request.text if request else None,
                before_tool_call=before_tool_call,
            ),
            timeout=policy.max_pass_seconds,
        )
        await before_tool_call()
        record.report = report
        record.gaps = list(dict.fromkeys(record.gaps + report.gaps))[-50:]
        record.last_context_hash, record.retry_after = context_hash, 0
        _restore_watch_state(record)
        if request:
            record.pending_requests = [
                queued for queued in record.pending_requests if queued.id != request.id
            ]
        note(record, "findings", report.summary)
        # Steering model: every pass is analyzed, but the thread only hears about it
        # when the findings actually moved. Direct questions are always answered.
        digest = service.fingerprint(
            [
                report.summary,
                report.impact,
                [hypothesis.model_dump() for hypothesis in report.hypotheses],
                report.questions,
            ]
        )
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
    except InvestigationStopped:
        _restore_watch_state(record)
    except Exception as exc:
        _failure(record, "investigation_failed", retry_after=getattr(exc, "retry_after", 60))
        logger.warning(
            "Investigation pass failed", extra={"investigation_id": record.id}, exc_info=True
        )
    await save(record)
    await service.wake()
    return {"status": record.status}
