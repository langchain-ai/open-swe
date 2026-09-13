"""Slack events for enrolled incident channels: enrollment, context, controls, and questions."""

import logging
import re
from typing import Any

from fastapi import BackgroundTasks, HTTPException

from agent.config import ENV
from agent.incidents import service, turns
from agent.incidents.access import is_observability_authorized
from agent.incidents.models import Incident, IncidentPolicy
from agent.incidents.presentation import report_message
from agent.input_messages import PersonIdentity
from agent.slack.client import (
    fetch_slack_thread_messages,
    get_slack_channel_info,
    get_slack_user_info,
    post_slack_thread_reply_with_ts,
    resolve_slack_thread_id,
    slack_message_bot_id,
)
from agent.slack.events import claim_slack_event
from agent.slack.http import slack_client
from agent.source_context import SlackThreadRef, SourceContext
from agent.store import store_client
from agent.utils.dashboard_links import dashboard_incident_url
from agent.webhooks.common import upsert_agent_thread_metadata

logger = logging.getLogger(__name__)

CONTROL_WORDS = frozenset({"pause", "resume", "complete", "reopen"})
START_PHRASES = frozenset(
    {"incident", "incidents", "start incident", "incidents start", "incident start"}
)
COMMAND_USAGE = (
    "Usage: `/openswe incidents` follows this channel as an incident; "
    "`/openswe incidents stop` ends it."
)
HISTORY_LIMIT = 100
_HANDLED_EVENTS = frozenset(
    {
        "channel_created",
        "channel_rename",
        "channel_archive",
        "message",
        "app_mention",
        "agent_session_stopped",
    }
)
_MEMBERSHIP_SUBTYPES = frozenset({"channel_join", "channel_leave", "group_join", "group_leave"})
# Bounded repetitions: the text is stripped first, and the gap between the mention and
# the request cannot backtrack polynomially on long runs of spaces.
_MENTION = re.compile(r"<@[A-Z0-9]{1,20}>[ \t]{0,64}(.*)", re.DOTALL)


def parse_mention(text: str) -> tuple[str, str]:
    """Return ("pause", "") for a bare control word, ("ask", question) otherwise."""
    match = _MENTION.fullmatch(text.strip())
    if not match:
        return "", ""
    rest = match.group(1).strip()
    if rest.lower() in CONTROL_WORDS:
        return rest.lower(), ""
    return ("ask", rest[:8000]) if rest else ("", "")


def _own_message(message: dict[str, Any], policy: IncidentPolicy) -> bool:
    bot_user = ENV.SLACK_BOT_USER_ID.get()
    profile = message.get("bot_profile")
    app_id = str(
        message.get("app_id") or (profile.get("app_id") if isinstance(profile, dict) else "") or ""
    )
    return bool(bot_user and message.get("user") == bot_user) or bool(
        policy.slack_app_id and app_id == policy.slack_app_id
    )


async def authorized_slack_user(user_id: str) -> PersonIdentity | None:
    """The responder identity for a Slack user allowed to control incidents, else None."""
    user = await get_slack_user_info(user_id)
    profile = (user or {}).get("profile")
    email = profile.get("email") if isinstance(profile, dict) else None
    if not is_observability_authorized(email if isinstance(email, str) else None):
        return None
    person: PersonIdentity = {"id": f"slack:{user_id}", "platform": "slack"}
    name = (
        (profile.get("display_name") or profile.get("real_name"))
        if isinstance(profile, dict)
        else ""
    ) or (user or {}).get("real_name")
    if isinstance(name, str) and name:
        person["display_name"] = name
    if isinstance(email, str) and email:
        person["email"] = email
    return person


def _introduction(record: Incident) -> str:
    text = "Incidents is following this channel. Findings will appear here"
    link = dashboard_incident_url(record.id)
    if link:
        text += f"; the full incident is at <{link}|Open incident>"
    return (
        text + ". Mention me with a question. To turn it off, mention me with `pause` to stop "
        "automatic analysis or `complete` to close the incident (or run `/openswe incidents stop`); "
        "`resume` turns it back on."
    )


async def _post(
    record: Incident, text: str, *, blocks: list[dict[str, Any]] | None = None
) -> str | None:
    if record.is_archived:
        return None
    ts, error = await post_slack_thread_reply_with_ts(
        record.channel_id,
        turns.SESSION_TS,
        text,
        blocks=blocks,
        unfurl_links=False,
        unfurl_media=False,
    )
    if error:
        logger.warning(
            "Incident notice not delivered", extra={"incident_id": record.id, "slack_error": error}
        )
    return ts


async def enroll_channel(
    channel_id: str, channel_name: str, policy: IncidentPolicy, *, manual: bool = False
) -> Incident | None:
    """Join a channel and bind it to a new system-owned agent thread.

    Automatic enrollment requires the channel prefix; a manual start by a responder does not.
    """
    incident_id = service.incident_id(policy.workspace_id, channel_id)
    if await service.INCIDENTS.get(incident_id):
        return None
    record = Incident(
        id=incident_id,
        workspace_id=policy.workspace_id,
        channel_id=channel_id,
        channel_name=channel_name,
        title=channel_name,
    )
    try:
        async with slack_client(token=ENV.SLACK_BOT_TOKEN.get()) as slack:
            await slack.conversations_join(channel=channel_id)
        info = await get_slack_channel_info(channel_id, use_cache=False)
        if not info or not service.channel_allowed(info, policy, require_prefix=not manual):
            record.status, record.reason = "needs_attention", "setup_failed"
            service.note(record, "error", "Channel is not an eligible public internal channel.")
            await service.save(record)
            return record
        record.channel_name = str(info.get("name") or channel_name)
        record.title = record.channel_name
        client = store_client()
        record.thread_id = await resolve_slack_thread_id(client, channel_id, turns.SESSION_TS)
        persisted = await upsert_agent_thread_metadata(
            record.thread_id,
            source="incidents_agent",
            title=record.channel_name,
            source_context=SourceContext(
                slack_thread=SlackThreadRef(channel_id=channel_id, thread_ts=turns.SESSION_TS)
            ),
            visibility="public",
            owner_type="system",
        )
        if not persisted:
            raise RuntimeError("could not persist incident thread metadata")
        await client.threads.update(
            thread_id=record.thread_id, metadata={"incident_id": incident_id}
        )
        record.anchor_ts = await _post(record, _introduction(record))
        service.note(record, "enrolled", f"Following #{record.channel_name}.")
        await service.save(record)
        history = await fetch_slack_thread_messages(channel_id, turns.SESSION_TS)
        queued = 0
        for message in history[-HISTORY_LIMIT:]:
            if _is_context(message, policy):
                queued += int(await turns.queue_context(record, message))
        # A brand-new channel has nothing to analyze yet; the first alert starts the first turn.
        if queued:
            await turns.schedule_automatic_turn(record, policy)
    except Exception:
        logger.exception("Incident enrollment failed", extra={"incident_id": incident_id})
        record.status, record.reason = "needs_attention", "setup_failed"
        service.note(record, "error", "Channel setup failed. Check Slack grants and configuration.")
        await service.save(record)
    return record


def _is_context(message: dict[str, Any], policy: IncidentPolicy) -> bool:
    subtype = str(message.get("subtype") or "")
    return (
        subtype not in _MEMBERSHIP_SUBTYPES
        and subtype != "message_deleted"
        and not _own_message(message, policy)
        and bool(turns.message_text(message))
    )


async def _reply_in_thread(channel_id: str, thread_ts: str, text: str) -> None:
    await post_slack_thread_reply_with_ts(
        channel_id, thread_ts, text, unfurl_links=False, unfurl_media=False
    )


async def start_incident(channel_id: str, user_id: str, background_tasks: BackgroundTasks) -> str:
    """Follow the current channel on a responder's request; returns the reply to show them."""
    policy = await service.get_policy()
    if not policy.enabled:
        return "Incidents is not enabled in this workspace."
    actor = await authorized_slack_user(user_id)
    if actor is None:
        return "Only incident responders can start an incident."
    record = await service.INCIDENTS.get(service.incident_id(policy.workspace_id, channel_id))
    if record is not None:
        if record.is_archived:
            return "This channel is archived, so Incidents cannot follow it."
        if record.status in {"paused", "completed"}:
            action = "reopen" if record.status == "completed" else "resume"
            background_tasks.add_task(apply_control, record, action, dict(actor))
            return "Incidents is following this channel again."
        return "Incidents is already following this channel."
    info = await get_slack_channel_info(channel_id, use_cache=False)
    if info is None or not service.channel_allowed(info, policy, require_prefix=False):
        return "Incidents can only follow a public internal channel that is not excluded."
    name = str(info.get("name") or channel_id)
    background_tasks.add_task(enroll_channel, channel_id, name, policy, manual=True)
    return f"Incidents is joining #{name}; findings will appear in the channel."


async def stop_incident(channel_id: str, user_id: str, background_tasks: BackgroundTasks) -> str:
    """Complete the current channel's incident on a responder's request."""
    policy = await service.get_policy()
    record = await service.INCIDENTS.get(service.incident_id(policy.workspace_id, channel_id))
    if record is None:
        return "Incidents is not following this channel."
    actor = await authorized_slack_user(user_id)
    if actor is None:
        return "Only incident responders can stop an incident."
    if record.status == "completed":
        return "This incident is already complete."
    background_tasks.add_task(apply_control, record, "complete", dict(actor))
    return "Incidents will stop following this channel and post the final summary."


async def slash_command(
    text: str,
    channel_id: str,
    user_id: str,
    team_id: str,
    api_app_id: str,
    background_tasks: BackgroundTasks,
) -> str:
    """`/openswe incidents [start|stop]`, run in the channel the command was typed in."""
    policy = await service.get_policy()
    if team_id != policy.workspace_id or api_app_id != policy.slack_app_id:
        return "Incidents is not enabled for this workspace."
    words = text.lower().split()
    if not words or words[0] not in {"incident", "incidents"} or len(words) > 2:
        return COMMAND_USAGE
    subcommand = words[1] if len(words) == 2 else "start"
    if subcommand == "stop":
        return await stop_incident(channel_id, user_id, background_tasks)
    if subcommand != "start":
        return COMMAND_USAGE
    return await start_incident(channel_id, user_id, background_tasks)


async def apply_control(record: Incident, action: str, actor: dict[str, Any]) -> Incident:
    """Pause, resume, complete, or reopen an incident and tell the channel."""
    policy = await service.get_policy()
    blocks: list[dict[str, Any]] | None = None
    if action in {"pause", "complete"}:
        await turns.cancel_active_runs(record.thread_id)
        record.status = "completed" if action == "complete" else "paused"
        record.reason = str(actor.get("reason") or f"responder_{action}")
        text = (
            "Incident complete."
            if action == "complete"
            else "Incident paused. Mention me with `resume` to continue."
        )
        if action == "complete":
            latest = await service.REPORTS.get(record.id)
            if latest is not None:
                text, blocks = report_message(
                    latest.report,
                    "Incident complete. " + latest.report.summary,
                    dashboard_incident_url(record.id),
                    reason="completion",
                )
    elif action in {"resume", "reopen"}:
        record.status, record.reason = "watching", ""
        text = "Incident watching resumed."
    else:
        raise ValueError(f"unknown incident control {action!r}")
    service.note(record, "control", text.split("\n", 1)[0])
    await service.save(record)
    await _post(record, text, blocks=blocks)
    if action in {"resume", "reopen"} and await turns.queued_context_count(record.thread_id):
        await turns.schedule_automatic_turn(record, policy)
    return record


async def handle_slack_event(
    payload: dict[str, Any], background_tasks: BackgroundTasks
) -> dict[str, str] | None:
    """Handle a Slack event for an enrolled channel; None lets the regular path continue."""
    event = payload.get("event") or {}
    kind = str(event.get("type") or "")
    if kind not in _HANDLED_EVENTS:
        return None
    channel = event.get("channel")
    channel_id = channel.get("id", "") if isinstance(channel, dict) else channel
    if not isinstance(channel_id, str) or not channel_id:
        return None
    policy = await service.get_policy()
    record = await service.INCIDENTS.get(service.incident_id(policy.workspace_id, channel_id))
    enrollment = (
        kind in {"channel_created", "channel_rename"}
        and policy.enabled
        and isinstance(channel, dict)
        and str(channel.get("name", "")).startswith(policy.channel_prefix)
        and channel_id not in policy.excluded_channel_ids
    )
    mention_user = event.get("user") if kind == "app_mention" else None
    manual_start = (
        record is None
        and isinstance(mention_user, str)
        and bool(mention_user)
        and parse_mention(str(event.get("text") or ""))[1].lower() in START_PHRASES
    )
    if record is None and not enrollment and not manual_start:
        return None
    if (
        payload.get("team_id") != policy.workspace_id
        or payload.get("api_app_id") != policy.slack_app_id
    ):
        raise HTTPException(401, "Slack workspace or app does not match Incidents policy")
    event_id = str(payload.get("event_id") or "")
    event_ts = str(event.get("event_ts") or event.get("ts") or "")
    if not await claim_slack_event(event_id, channel_id, event_ts):
        return {"status": "duplicate"}
    if manual_start:
        assert isinstance(mention_user, str)
        reply = await start_incident(channel_id, mention_user, background_tasks)
        background_tasks.add_task(_reply_in_thread, channel_id, str(event.get("ts") or ""), reply)
        return {"status": "accepted"}
    if record is None:
        assert isinstance(channel, dict)
        background_tasks.add_task(enroll_channel, channel_id, str(channel.get("name")), policy)
        return {"status": "accepted"}
    if kind in {"channel_created", "channel_rename"}:
        return {"status": "ignored"}
    if record.channel_id in policy.excluded_channel_ids:
        return {"status": "ignored"}
    if kind == "channel_archive":
        record.is_archived = True
        if record.status != "completed":
            background_tasks.add_task(
                apply_control,
                record,
                "complete",
                {"id": "system:slack", "reason": "channel_archived"},
            )
        else:
            await service.save(record)
        return {"status": "accepted"}
    if not policy.enabled:
        return {"status": "ignored"}
    if kind == "agent_session_stopped":
        user = event.get("user")
        actor = await authorized_slack_user(user) if isinstance(user, str) and user else None
        if actor is not None and record.status in {"watching", "needs_attention"}:
            background_tasks.add_task(apply_control, record, "pause", dict(actor))
        return {"status": "accepted"}
    message = event.get("message") if event.get("subtype") == "message_changed" else event
    if not isinstance(message, dict):
        return {"status": "ignored"}
    text = str(message.get("text") or "")
    bot_user = ENV.SLACK_BOT_USER_ID.get()
    user = message.get("user")
    mentioned = kind == "app_mention" or bool(bot_user and f"<@{bot_user}>" in text)
    if mentioned and isinstance(user, str) and user and not slack_message_bot_id(message):
        actor = await authorized_slack_user(user)
        if actor is None:
            return {"status": "ignored"}
        action, question = parse_mention(text)
        if action in CONTROL_WORDS:
            background_tasks.add_task(apply_control, record, action, dict(actor))
            return {"status": "accepted"}
        if action == "ask":
            ts = str(message.get("ts") or "")
            thread_ts = str(message.get("thread_ts") or "")
            background_tasks.add_task(
                turns.dispatch_turn,
                record,
                policy,
                request=question,
                requester=actor,
                reply_thread_ts=thread_ts if thread_ts and thread_ts != ts else "",
            )
            return {"status": "accepted"}
        return {"status": "ignored"}
    if record.status == "completed" or not _is_context(message, policy):
        return {"status": "ignored"}
    await turns.queue_context(record, message)
    if record.status in {"watching", "needs_attention"}:
        background_tasks.add_task(turns.schedule_automatic_turn, record, policy)
    return {"status": "accepted"}
