"""Helpers for collaborative commit and PR attribution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .github_user_email_map import GITHUB_USER_EMAIL_MAP

logger = logging.getLogger(__name__)

OPEN_SWE_BOT_NAME = "open-swe[bot]"
OPEN_SWE_BOT_EMAIL = "open-swe@users.noreply.github.com"


@dataclass(frozen=True)
class CollaboratorIdentity:
    """Identity used for git trailers and PR attribution."""

    display_name: str
    commit_name: str
    commit_email: str


def _normalize_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _github_noreply_email(login: str, user_id: Any = None) -> str:
    normalized_login = _normalize_text(login)
    if not normalized_login:
        return ""

    normalized_user_id = str(user_id).strip() if user_id is not None else ""
    if normalized_user_id:
        return f"{normalized_user_id}+{normalized_login}@users.noreply.github.com"
    return f"{normalized_login}@users.noreply.github.com"


def _identity_from_github_token(github_token: str | None) -> CollaboratorIdentity | None:
    if not github_token:
        return None

    try:
        response = httpx.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=5.0,
        )
        if response.status_code != 200:  # noqa: PLR2004
            logger.debug("GitHub user lookup returned %s", response.status_code)
            return None

        payload = response.json()
        login = _normalize_text(payload.get("login"))
        display_name = _normalize_text(payload.get("name")) or login
        commit_email = _github_noreply_email(login, payload.get("id")) or _normalize_text(
            payload.get("email")
        )
        if not display_name or not commit_email:
            return None
        if commit_email == OPEN_SWE_BOT_EMAIL and display_name == OPEN_SWE_BOT_NAME:
            return None
        return CollaboratorIdentity(
            display_name=display_name,
            commit_name=display_name,
            commit_email=commit_email,
        )
    except httpx.HTTPError:
        logger.debug("Failed to resolve GitHub user identity from token", exc_info=True)
        return None


def _identity_from_config(config: dict[str, Any]) -> CollaboratorIdentity | None:
    configurable = config.get("configurable", {})

    github_login = _normalize_text(configurable.get("github_login"))
    if github_login:
        github_user_id = configurable.get("github_user_id")
        commit_email = _github_noreply_email(github_login, github_user_id) or _normalize_text(
            GITHUB_USER_EMAIL_MAP.get(github_login)
        )
        if commit_email:
            return CollaboratorIdentity(
                display_name=github_login,
                commit_name=github_login,
                commit_email=commit_email,
            )

    slack_thread = configurable.get("slack_thread", {})
    linear_issue = configurable.get("linear_issue", {})

    display_name = (
        _normalize_text(slack_thread.get("triggering_user_name"))
        or _normalize_text(linear_issue.get("triggering_user_name"))
        or _normalize_text(configurable.get("user_email")).split("@", 1)[0]
    )
    commit_email = _normalize_text(configurable.get("user_email")) or _normalize_text(
        slack_thread.get("triggering_user_email")
    )
    if display_name and commit_email:
        return CollaboratorIdentity(
            display_name=display_name,
            commit_name=display_name,
            commit_email=commit_email,
        )
    return None


def resolve_triggering_user_identity(
    config: dict[str, Any],
    github_token: str | None = None,
) -> CollaboratorIdentity | None:
    """Resolve the triggering user's git identity.

    Prefer the GitHub account identity derived from the token when available.
    Fall back to config metadata when the run originated from GitHub or when
    Slack/Linear supplied an explicit user name and email.
    """

    return _identity_from_github_token(github_token) or _identity_from_config(config)


def add_user_coauthor_trailer(
    commit_message: str,
    identity: CollaboratorIdentity | None,
) -> str:
    """Append a Co-authored-by trailer when a user identity is available."""
    normalized_message = commit_message.rstrip()
    if not identity:
        return normalized_message

    trailer = f"Co-authored-by: {identity.commit_name} <{identity.commit_email}>"
    if trailer in normalized_message:
        return normalized_message
    return f"{normalized_message}\n\n{trailer}"


def add_pr_collaboration_note(
    pr_body: str,
    identity: CollaboratorIdentity | None,
) -> str:
    """Append a best-effort PR attribution note.

    GitHub supports commit co-authors, but not PR co-authors. This note makes
    the collaboration explicit in the automatically-opened PR body.
    """

    normalized_body = pr_body.rstrip()
    if not identity:
        return normalized_body

    note = f"_Opened collaboratively by {identity.display_name} and open-swe._"
    if note in normalized_body:
        return normalized_body
    if not normalized_body:
        return note
    return f"{normalized_body}\n\n{note}"
