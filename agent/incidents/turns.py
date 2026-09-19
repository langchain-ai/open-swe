"""One LangGraph run per incident turn, scheduled from Slack events and run completions."""

import json
import logging
from typing import Any, Literal

import httpx

from agent.dashboard.options import normalize_model_choice
from agent.dispatch import create_durable_run
from agent.incidents import service
from agent.incidents.evidence_tools import redact
from agent.incidents.models import Incident, IncidentPolicy
from agent.incidents.report import CONTEXT_MARKER
from agent.input_messages import (
    PersonIdentity,
    SystemIdentity,
    human_input,
    person_introduction,
    system_input,
    system_introduction,
)
from agent.slack.client import (
    post_slack_thread_reply_with_ts,
    slack_message_bot_id,
    slack_message_bot_name,
)
from agent.store import store_client
from agent.utils.thread_ops import queue_message_for_thread

logger = logging.getLogger(__name__)

# The whole channel is one conversation, like a code channel session.
SESSION_TS = "0"
AUTOMATIC_DELAY_SECONDS = 15
AUTOMATIC_REQUEST = (
    "New activity arrived in the incident channel. Review the new context messages, "
    "update your investigation, and record the report."
)
FAILURE_NOTICE = (
    "The incident agent hit an error on its last turn. Mention me with a question to retry."
)
_INCIDENTS_SYSTEM: SystemIdentity = {
    "id": "system:incidents",
    "display_name": "Incidents",
    "platform": "open-swe",
}


def permalink(channel_id: str, ts: str) -> str:
    return f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"


def author_of(message: dict[str, Any]) -> str:
    bot_id = slack_message_bot_id(message)
    if bot_id:
        return slack_message_bot_name(message) or f"bot {bot_id}"
    user = str(message.get("user") or "")
    return f"<@{user}>" if user else "unknown"


def message_text(message: dict[str, Any]) -> str:
    text = str(message.get("text") or "")
    for attachment in message.get("attachments") or []:
        if isinstance(attachment, dict):
            text += "\n" + str(attachment.get("text") or attachment.get("fallback") or "")
    for block in message.get("blocks") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), dict):
            value = block["text"].get("text", "")
            if value and value not in text:
                text += "\n" + str(value)
    return text.strip()


def context_block(channel_id: str, message: dict[str, Any]) -> dict[str, Any]:
    """Queue payload for one Slack message: a citable header line plus redacted text."""
    ts = str(message.get("ts") or "")
    edited = message.get("edited")
    edited_ts = str(edited.get("ts") or "") if isinstance(edited, dict) else ""
    evidence_id = f"slack:{ts}" + (f":{edited_ts}" if edited_ts else "")
    header = {
        "evidence_id": evidence_id,
        "source_url": permalink(channel_id, ts),
        "author": author_of(message),
        "ts": ts,
    }
    payload: dict[str, Any] = {
        "text": f"{CONTEXT_MARKER}{json.dumps(header)}\n{redact(message_text(message), 8000)}",
        "queue_id": f"incident:{channel_id}:{evidence_id}",
    }
    user = message.get("user")
    if isinstance(user, str) and user and not slack_message_bot_id(message):
        payload["sender"] = {
            "id": f"slack:{user}",
            "display_name": f"<@{user}>",
            "platform": "slack",
        }
    return payload


async def queue_context(record: Incident, message: dict[str, Any]) -> bool:
    return await queue_message_for_thread(
        record.thread_id, context_block(record.channel_id, message)
    )


async def _runs(thread_id: str, status: Literal["pending", "running"]) -> list[dict[str, Any]]:
    try:
        return [
            dict(run) for run in await store_client().runs.list(thread_id, status=status) if run
        ]
    except Exception:  # noqa: BLE001
        logger.debug("Could not list %s runs for %s", status, thread_id, exc_info=True)
        return []


async def has_active_run(thread_id: str) -> bool:
    if not thread_id:
        return False
    return bool(await _runs(thread_id, "pending") or await _runs(thread_id, "running"))


async def cancel_active_runs(thread_id: str, *, keep_run_id: str = "") -> None:
    """Interrupt pending and running runs, except the one carrying out the request."""
    if not thread_id:
        return
    runs = [*await _runs(thread_id, "pending"), *await _runs(thread_id, "running")]
    run_ids = [
        str(run["run_id"])
        for run in runs
        if run.get("run_id") and str(run["run_id"]) != keep_run_id
    ]
    if run_ids:
        await store_client().runs.cancel_many(
            thread_id=thread_id, run_ids=run_ids, action="interrupt"
        )


async def queued_context_count(thread_id: str) -> int:
    """Count of messages waiting to reach this thread's agent.

    Combines the legacy in-run injection queue (``queue_context`` /
    ``queue_message_for_thread``, still used by Slack context and the
    ``send_dashboard_message`` agent tool) with genuine pending runs
    (a composer follow-up enqueued via the server-backed queue adapter is a
    real LangGraph run, not a KV-store entry, and would otherwise be
    invisible here).
    """
    try:
        item = await store_client().store.get_item(("queue", thread_id), "pending_messages")
    except Exception:  # noqa: BLE001
        item = None
    value = item.get("value") if isinstance(item, dict) else None
    messages = value.get("messages") if isinstance(value, dict) else None
    kv_count = len(messages) if isinstance(messages, list) else 0
    return kv_count + len(await _runs(thread_id, "pending"))


def _configurable(
    record: Incident, policy: IncidentPolicy, *, request: str | None, reply_thread_ts: str
) -> dict[str, Any]:
    slack_thread: dict[str, Any] = {"channel_id": record.channel_id, "thread_ts": SESSION_TS}
    if reply_thread_ts:
        slack_thread["reply_thread_ts"] = reply_thread_ts
    configurable: dict[str, Any] = {
        "thread_id": record.thread_id,
        "source": "incidents_agent",
        "incident_id": record.id,
        "slack_thread": slack_thread,
        # Always written, never omitted: a thread keeps the configurable of its earlier runs,
        # so an automatic turn that left this out would inherit the last responder question
        # and keep answering it as though someone had just asked.
        "incident_request": request,
    }
    if policy.model:
        model, effort = normalize_model_choice(policy.model, None)
        configurable["agent_model_id"] = model
        configurable["agent_effort"] = effort
    return configurable


def _input(
    record: Incident, *, request: str | None, requester: PersonIdentity | None
) -> dict[str, Any]:
    channel = f"slack:{record.channel_id}"
    if request is None:
        return {
            "messages": [
                system_introduction(_INCIDENTS_SYSTEM),
                system_input(
                    AUTOMATIC_REQUEST,
                    {
                        "sender_id": _INCIDENTS_SYSTEM["id"],
                        "channel_id": channel,
                        "surface": "slack",
                        "kind": "system",
                    },
                ),
            ]
        }
    person: PersonIdentity = requester or {
        "id": "system:incidents-dashboard",
        "platform": "open-swe",
    }
    return {
        "messages": [
            person_introduction(person),
            human_input(
                request,
                {
                    "sender_id": person["id"],
                    "channel_id": channel,
                    "surface": "slack",
                    "kind": "human",
                },
            ),
        ]
    }


async def dispatch_turn(
    record: Incident,
    policy: IncidentPolicy,
    *,
    request: str | None = None,
    requester: PersonIdentity | None = None,
    reply_thread_ts: str = "",
    after_seconds: float | None = None,
    multitask_strategy: str | None = None,
) -> dict[str, Any]:
    """Start one main-agent run on the incident thread.

    An explicit request interrupts whatever is in flight, like a tagged Slack message;
    an automatic turn queues behind it.
    """
    explicit = request is not None
    run = await create_durable_run(
        record.thread_id,
        "agent",
        input=_input(record, request=request, requester=requester),
        source="incidents_agent",
        config={
            "configurable": _configurable(
                record, policy, request=request, reply_thread_ts=reply_thread_ts
            )
        },
        metadata={
            "source": "incidents_agent",
            "incident_id": record.id,
            "incident_turn": "explicit" if explicit else "automatic",
        },
        multitask_strategy=multitask_strategy or ("interrupt" if explicit else "enqueue"),
        after_seconds=after_seconds,
    )
    return dict(run)


async def schedule_automatic_turn(record: Incident, policy: IncidentPolicy) -> bool:
    """Schedule a debounced automatic turn unless one is already pending or running."""
    if record.status not in {"watching", "needs_attention"} or not record.thread_id:
        return False
    if await has_active_run(record.thread_id):
        return False
    # "reject" makes the platform refuse a second run while one is pending or running,
    # so two events racing past the check above still produce a single turn.
    try:
        await dispatch_turn(
            record, policy, after_seconds=AUTOMATIC_DELAY_SECONDS, multitask_strategy="reject"
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            return False
        raise
    return True


async def handle_run_completion(thread_id: str, run_id: str | None, status: str) -> dict[str, str]:
    """Follow up a finished incident run: reschedule stranded context or note a failure."""
    records = await service.INCIDENTS.search(filter={"thread_id": thread_id})
    record = records[0] if records else None
    if record is None:
        return {"status": "ignored", "reason": "unknown incident thread"}
    if status == "success":
        policy = await service.get_policy()
        if (
            policy.enabled
            and record.status in {"watching", "needs_attention"}
            and await queued_context_count(thread_id)
            and not await has_active_run(thread_id)
        ):
            await dispatch_turn(record, policy, after_seconds=AUTOMATIC_DELAY_SECONDS)
            return {"status": "ok", "reason": "queued incident context rescheduled"}
        return {"status": "ok", "reason": "incident turn complete"}
    if status not in {"error", "timeout"}:
        return {"status": "ignored", "reason": f"incident run status {status}"}
    if run_id and record.last_failure_run_id == run_id:
        return {"status": "ignored", "reason": "incident failure already noted"}
    record.last_failure_run_id = run_id or ""
    if record.status == "watching":
        record.status, record.reason = "needs_attention", "run_failed"
    service.note(record, "error", FAILURE_NOTICE)
    await service.save(record)
    if not record.is_archived:
        await post_slack_thread_reply_with_ts(
            record.channel_id, SESSION_TS, FAILURE_NOTICE, unfurl_links=False, unfurl_media=False
        )
    return {"status": "ok", "reason": "incident failure noted"}
