"""Resolve verified participants for the active agent thread."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

from ..dashboard.user_mappings import get_mapping, login_for_email, login_for_slack_id
from .github_comments import fetch_github_thread_participants
from .github_token import get_github_token
from .json_types import as_json_object, thread_metadata
from .linear import fetch_linear_issue_participant_emails
from .slack import fetch_slack_thread_messages

PARTICIPANT_LOGINS_KEY = "participant_logins"
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


def merge_participant_logins(existing: Any, *logins: Any) -> list[str]:
    merged: dict[str, str] = {}
    if isinstance(existing, list):
        for value in existing:
            if isinstance(value, str) and value.strip():
                merged[value.strip().lower()] = value.strip()
    for value in logins:
        if isinstance(value, str) and value.strip():
            merged[value.strip().lower()] = value.strip()
    return [merged[key] for key in sorted(merged)]


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


def _context_value(configurable: dict[str, Any], metadata: dict[str, Any], key: str) -> Any:
    value = configurable.get(key)
    if value is not None:
        return value
    source_context = metadata.get("source_context")
    if isinstance(source_context, dict):
        return source_context.get(key)
    return None


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
        merge_participant_logins(
            metadata.get(PARTICIPANT_LOGINS_KEY),
            metadata.get("github_login"),
            configurable.get("github_login"),
        )
    )
    logins, unresolved_count = await _mapped_github_logins(candidate_logins)

    slack_thread = _context_value(configurable, metadata, "slack_thread")
    linear_issue = _context_value(configurable, metadata, "linear_issue")
    github_issue = _context_value(configurable, metadata, "github_issue")
    source = configurable.get("source") or metadata.get("source")

    if isinstance(slack_thread, dict):
        channel_id = slack_thread.get("channel_id")
        thread_ts = slack_thread.get("thread_ts")
        if not isinstance(channel_id, str) or not channel_id or not isinstance(thread_ts, str):
            return None, 0, "Slack thread context is incomplete"
        messages = await fetch_slack_thread_messages(channel_id, thread_ts)
        if not messages:
            return None, 0, "Could not verify Slack thread participants"
        mapped, source_unresolved = await _mapped_slack_logins(messages)
        logins.update(mapped)
        unresolved_count += source_unresolved
    elif isinstance(linear_issue, dict):
        issue_id = linear_issue.get("id")
        if not isinstance(issue_id, str) or not issue_id:
            return None, 0, "Linear issue context is incomplete"
        emails = await fetch_linear_issue_participant_emails(issue_id)
        if emails is None:
            return None, 0, "Could not verify Linear issue participants"
        mapped, source_unresolved = await _mapped_email_logins(emails)
        logins.update(mapped)
        unresolved_count += source_unresolved
    elif isinstance(github_issue, dict) or (
        source == "github" and _context_value(configurable, metadata, "pr_number") is not None
    ):
        issue_number = (
            github_issue.get("number")
            if isinstance(github_issue, dict)
            else configurable.get("pr_number")
        )
        if not isinstance(issue_number, int):
            context_pr_number = _context_value(configurable, metadata, "pr_number")
            issue_number = context_pr_number if isinstance(context_pr_number, int) else None
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
    participants, _, error = await resolve_thread_participant_logins(get_config())
    if participants is None:
        raise ValueError(error or "Could not verify thread participants")
    matches = {participant.lower(): participant for participant in participants}
    if login.lower() not in matches:
        raise ValueError(f"{login!r} is not a verified participant in this thread.")
    return matches[login.lower()]
