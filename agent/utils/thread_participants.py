"""Resolve verified participants for the active agent thread."""

import asyncio
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

from agent.dashboard.agent_overrides import resolve_github_login
from agent.dashboard.user_mappings import get_mapping, login_for_email, login_for_slack_id
from agent.github.comments import fetch_github_thread_participants
from agent.github.thread_token import get_github_token
from agent.linear.client import fetch_linear_issue_participant_emails
from agent.slack.client import fetch_slack_thread_messages
from agent.source_context import SourceContext
from agent.utils.json_types import as_json_object, thread_metadata

PARTICIPANT_LOGINS_KEY = "participant_logins"
# Slack and Linear senders who have no GitHub mapping are still participants;
# their email is the only identifier the thread ever learns.
PARTICIPANT_EMAILS_KEY = "participant_emails"
_SLACK_SYSTEM_MESSAGE_SUBTYPES = {
    "bot_message",
    "channel_archive",
    "channel_join",
    "channel_leave",
    "channel_name",
    "channel_purpose",
    "channel_topic",
    "channel_unarchive",
    "group_join",
    "group_leave",
    "message_changed",
    "message_deleted",
    "pinned_item",
    "slackbot_response",
    "unpinned_item",
}


def participant_search_filters(login: str, email: str | None = None) -> list[dict[str, Any]]:
    """Metadata filters matching threads this person has participated in."""
    filters = [{PARTICIPANT_LOGINS_KEY: {login.strip().lower(): True}}]
    if isinstance(email, str) and email.strip():
        filters.append({PARTICIPANT_EMAILS_KEY: {email.strip().lower(): True}})
    return filters


def merge_participants(existing: Any, *values: Any) -> dict[str, bool]:
    """Participants as a key-per-person map so metadata search can match one entry.

    JSONB containment only reaches inside objects, so a list would force an
    exact-match filter on the whole set.
    """
    merged = dict.fromkeys(participant_logins(existing), True)
    for value in values:
        if isinstance(value, str) and value.strip():
            merged[value.strip().lower()] = True
    return dict(sorted(merged.items()))


def participant_logins(stored: Any) -> list[str]:
    if isinstance(stored, Mapping):
        return sorted(key.strip().lower() for key in stored if isinstance(key, str) and key.strip())
    if isinstance(stored, list):
        return sorted(
            {value.strip().lower() for value in stored if isinstance(value, str) and value.strip()}
        )
    return []


async def _active_mapping_login(login: str | None) -> str | None:
    if not isinstance(login, str) or not login.strip():
        return None
    record = await get_mapping(login.strip())
    if not record or record.get("status", "active") != "active":
        return None
    value = record.get("github_login")
    return value.strip() if isinstance(value, str) and value.strip() else None


async def _mapped_slack_logins(messages: list[dict[str, Any]]) -> tuple[set[str], int]:
    user_ids = {
        user_id
        for message in messages
        if not message.get("bot_id")
        and not message.get("bot_profile")
        and message.get("subtype") not in _SLACK_SYSTEM_MESSAGE_SUBTYPES
        and isinstance(user_id := message.get("user"), str)
        and user_id
    }
    resolved = await asyncio.gather(*(login_for_slack_id(user_id) for user_id in user_ids))
    mapped = await asyncio.gather(*(_active_mapping_login(login) for login in resolved))
    return {login for login in mapped if login}, sum(login is None for login in mapped)


async def _mapped_email_logins(emails: set[str]) -> tuple[set[str], int]:
    resolved = await asyncio.gather(*(login_for_email(email) for email in emails))
    mapped = await asyncio.gather(*(_active_mapping_login(login) for login in resolved))
    return {login for login in mapped if login}, sum(login is None for login in mapped)


async def _mapped_github_logins(logins: set[str]) -> tuple[set[str], int]:
    return {login.strip() for login in logins if login.strip()}, 0


def _context(configurable: dict[str, Any], metadata: dict[str, Any]) -> SourceContext:
    """Thread source, with ``configurable`` taking precedence over metadata."""
    merged = SourceContext.from_metadata(metadata).dump()
    for key in ("slack_thread", "linear_issue", "github_issue", "pr_number"):
        value = configurable.get(key)
        if value is not None:
            merged[key] = value
    return SourceContext.parse(merged)


def _repo_config(configurable: dict[str, Any], metadata: dict[str, Any]) -> dict[str, str] | None:
    repo = configurable.get("repo") or metadata.get("repo")
    if (
        isinstance(repo, dict)
        and isinstance(repo.get("owner"), str)
        and isinstance(repo.get("name"), str)
    ):
        if repo["owner"] and repo["name"]:
            return {"owner": repo["owner"], "name": repo["name"]}
    owner = metadata.get("repo_owner")
    name = metadata.get("repo_name")
    if isinstance(owner, str) and owner and isinstance(name, str) and name:
        return {"owner": owner, "name": name}
    return None


async def resolve_thread_participant_logins(
    config: Mapping[str, Any],
) -> tuple[set[str] | None, int, str | None]:
    configurable = as_json_object(config.get("configurable"))
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return None, 0, "Missing thread_id in run config"

    try:
        thread = await get_client().threads.get(thread_id)
    except Exception:
        return None, 0, "Could not verify the active thread"
    metadata = thread_metadata(thread)

    candidate_logins = set(
        merge_participants(
            metadata.get(PARTICIPANT_LOGINS_KEY),
            configurable.get("github_login"),
        )
    )
    logins, unresolved_count = await _mapped_github_logins(candidate_logins)

    context = _context(configurable, metadata)
    source = configurable.get("source") or metadata.get("source")

    if context.slack_thread is not None:
        slack_thread = context.slack_thread
        if not slack_thread.channel_id:
            return None, 0, "Slack thread context is incomplete"
        messages = await fetch_slack_thread_messages(
            slack_thread.channel_id, slack_thread.thread_ts
        )
        if not messages:
            return None, 0, "Could not verify Slack thread participants"
        mapped, source_unresolved = await _mapped_slack_logins(messages)
        logins.update(mapped)
        unresolved_count += source_unresolved
    elif context.linear_issue is not None:
        if not context.linear_issue.id:
            return None, 0, "Linear issue context is incomplete"
        emails = await fetch_linear_issue_participant_emails(context.linear_issue.id)
        if emails is None:
            return None, 0, "Could not verify Linear issue participants"
        mapped, source_unresolved = await _mapped_email_logins(emails)
        logins.update(mapped)
        unresolved_count += source_unresolved
    elif context.github_issue is not None or (source == "github" and context.pr_number is not None):
        issue_number = (
            context.github_issue.number if context.github_issue else None
        ) or context.pr_number
        repo = _repo_config(configurable, metadata)
        token = get_github_token(config)
        if not repo or not issue_number or not token:
            return None, 0, "GitHub thread context is incomplete"
        participants = await fetch_github_thread_participants(repo, issue_number, token=token)
        if participants is None:
            return None, 0, "Could not verify GitHub thread participants"
        mapped, source_unresolved = await _mapped_github_logins(participants)
        logins.update(mapped)
        unresolved_count += source_unresolved
    elif source == "dashboard":
        if not metadata.get(PARTICIPANT_LOGINS_KEY):
            return None, 0, "Dashboard participant metadata is unavailable"
    elif source == "schedule":
        if not metadata.get(PARTICIPANT_LOGINS_KEY):
            return None, 0, "Schedule participant metadata is unavailable"
    else:
        return None, 0, "Unsupported or missing thread source"

    if not logins:
        return None, unresolved_count, "No mapped participants were found for the active thread"
    return logins, unresolved_count, None


async def resolve_participant(on_behalf_of: str) -> str:
    login = on_behalf_of.strip()
    if not login:
        raise ValueError("on_behalf_of is required: name the thread participant to act for.")
    config = get_config()
    caller = resolve_github_login(as_json_object(config))
    if not caller or login.lower() != caller.lower():
        raise ValueError("on_behalf_of must match the user who triggered this run.")
    participants, _, error = await resolve_thread_participant_logins(config)
    if participants is None:
        raise ValueError(error or "Could not verify thread participants")
    matches = {participant.lower(): participant for participant in participants}
    if login.lower() not in matches:
        raise ValueError(f"{login!r} is not a verified participant in this thread.")
    return matches[login.lower()]
