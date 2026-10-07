"""Resolve verified participants for the active agent thread."""

import asyncio
from collections.abc import Collection, Mapping
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.github.comments import fetch_github_thread_participants
from openswe.github.thread_token import resolve_thread_github_token
from openswe.input_messages import PersonIdentity
from openswe.slack.client import fetch_slack_thread_messages
from openswe.source_context import SourceContext
from openswe.users import User
from openswe.utils.json_types import as_json_object, thread_metadata

PARTICIPANT_IDENTITY_PREFIX = "participant_identity:"
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


def _legacy_people(metadata: Mapping[str, object]) -> list[PersonIdentity]:
    return [
        {"id": f"github:{login}", "github_login": login}
        for login in participant_logins(metadata.get(PARTICIPANT_LOGINS_KEY))
    ] + [
        {"id": f"email:{email}", "email": email}
        for email in participant_logins(metadata.get(PARTICIPANT_EMAILS_KEY))
    ]


def participant_ids(metadata: Mapping[str, object]) -> set[str]:
    """Stored people plus every legacy alias not yet covered by an ingress upgrade."""
    identities = {
        key.removeprefix(PARTICIPANT_IDENTITY_PREFIX): value
        for key, value in metadata.items()
        if key.startswith(PARTICIPANT_IDENTITY_PREFIX) and isinstance(value, str) and value
    }
    return set(identities.values()) | {
        person["id"] for person in _legacy_people(metadata) if person["id"] not in identities
    }


async def participant_metadata(
    existing: Mapping[str, object],
    *,
    login: str | None = None,
    email: str | None = None,
    people: Collection[PersonIdentity] = (),
) -> dict[str, object]:
    """Resolve participants at ingress, preserving legacy search fields and historical users."""
    logins = merge_participants(
        existing.get(PARTICIPANT_LOGINS_KEY),
        login,
        *(person.get("github_login") for person in people),
    )
    emails = merge_participants(
        existing.get(PARTICIPANT_EMAILS_KEY), email, *(person.get("email") for person in people)
    )
    update: dict[str, object] = {
        PARTICIPANT_LOGINS_KEY: logins,
        PARTICIPANT_EMAILS_KEY: emails,
    }
    candidates = {person["id"]: person for person in _legacy_people(update)}
    for key, value in existing.items():
        if key.startswith(PARTICIPANT_IDENTITY_PREFIX) and isinstance(value, str):
            alias = key.removeprefix(PARTICIPANT_IDENTITY_PREFIX)
            if value == alias and not alias.startswith("user:"):
                person: PersonIdentity = {"id": alias}
                if alias.startswith("email:"):
                    person["email"] = alias.removeprefix("email:")
                candidates.setdefault(alias, person)
    candidates = {
        alias: person
        for alias, person in candidates.items()
        if not str(existing.get(PARTICIPANT_IDENTITY_PREFIX + alias, "")).startswith("user:")
    }
    incoming = _legacy_people(
        {
            PARTICIPANT_LOGINS_KEY: merge_participants(None, login),
            PARTICIPANT_EMAILS_KEY: merge_participants(None, email),
        }
    )
    candidates.update((person["id"], person) for person in [*incoming, *people])
    resolved = dict(
        zip(
            candidates,
            await asyncio.gather(*(User.canonical_person(p) for p in candidates.values())),
            strict=True,
        )
    )
    for alias, person in resolved.items():
        key = PARTICIPANT_IDENTITY_PREFIX + alias
        previous = existing.get(key)
        identity = person["id"]
        if identity == alias and (login := person.get("github_login")):
            login_alias = f"github:{login.strip().lower()}"
            linked = resolved.get(login_alias, {}).get("id") or existing.get(
                PARTICIPANT_IDENTITY_PREFIX + login_alias
            )
            if isinstance(linked, str) and linked.startswith("user:"):
                identity = linked
        if isinstance(previous, str) and previous.startswith("user:"):
            if not identity.startswith("user:"):
                continue
            if previous != identity:
                update[PARTICIPANT_IDENTITY_PREFIX + previous] = previous
        if identity.startswith("user:"):
            update[PARTICIPANT_IDENTITY_PREFIX + identity] = identity
        update[key] = identity
    return update


async def _active_mapping_login(login: str | None) -> str | None:
    if not isinstance(login, str) or not login.strip():
        return None
    user = await User.for_login("github", login.strip())
    return (user.github_login or None) if user is not None else None


def slack_participant_ids(messages: Collection[Mapping[str, object]]) -> set[str]:
    return {
        user_id
        for message in messages
        if not message.get("bot_id")
        and not message.get("bot_profile")
        and message.get("subtype") not in _SLACK_SYSTEM_MESSAGE_SUBTYPES
        and isinstance(user_id := message.get("user"), str)
        and user_id
    }


async def _mapped_slack_logins(messages: list[dict[str, Any]]) -> tuple[set[str], int]:
    user_ids = slack_participant_ids(messages)
    resolved = await asyncio.gather(*(User.login_for_slack(user_id) for user_id in user_ids))
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
) -> tuple[set[str], int]:
    configurable = as_json_object(config.get("configurable"))
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("Missing thread_id in run config")

    try:
        thread = await get_client().threads.get(thread_id)
    except Exception as exc:
        raise ValueError("Could not verify the active thread") from exc
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
            raise ValueError("Slack thread context is incomplete")
        messages = await fetch_slack_thread_messages(
            slack_thread.channel_id, slack_thread.thread_ts
        )
        if not messages:
            raise ValueError("Could not verify Slack thread participants")
        mapped, source_unresolved = await _mapped_slack_logins(messages)
        logins.update(mapped)
        unresolved_count += source_unresolved
    elif context.linear_issue is not None:
        if not logins:
            raise ValueError("Linear participant metadata is unavailable")
    elif context.github_issue is not None or (source == "github" and context.pr_number is not None):
        issue_number = (
            context.github_issue.number if context.github_issue else None
        ) or context.pr_number
        repo = _repo_config(configurable, metadata)
        token = await resolve_thread_github_token(config)
        if not repo or not issue_number or not token:
            raise ValueError("GitHub thread context is incomplete")
        participants = await fetch_github_thread_participants(repo, issue_number, token=token)
        if participants is None:
            raise ValueError("Could not verify GitHub thread participants")
        mapped, source_unresolved = await _mapped_github_logins(participants)
        logins.update(mapped)
        unresolved_count += source_unresolved
    elif source == "dashboard":
        if not metadata.get(PARTICIPANT_LOGINS_KEY):
            raise ValueError("Dashboard participant metadata is unavailable")
    elif source == "schedule":
        if not metadata.get(PARTICIPANT_LOGINS_KEY):
            raise ValueError("Schedule participant metadata is unavailable")
    else:
        raise ValueError("Unsupported or missing thread source")

    if not logins:
        raise ValueError("No mapped participants were found for the active thread")
    return logins, unresolved_count


async def resolve_participant(on_behalf_of: str) -> str:
    login = on_behalf_of.strip()
    if not login:
        raise ValueError("on_behalf_of is required: name the thread participant to act for.")
    config = get_config()
    caller = await resolve_github_login(as_json_object(config))
    if not caller or login.lower() != caller.lower():
        raise ValueError("on_behalf_of must match the user who triggered this run.")
    participants, _ = await resolve_thread_participant_logins(config)
    matches = {participant.lower(): participant for participant in participants}
    if login.lower() not in matches:
        raise ValueError(f"{login!r} is not a verified participant in this thread.")
    return matches[login.lower()]
