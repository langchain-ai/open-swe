"""Helpers for collaborative commit and PR attribution."""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx2

from agent.analytics.identity import DisplayNameSource
from agent.users import User
from agent.utils import ttl_cache
from agent.utils.http import DEFAULT_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

_GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
_PUBLIC_PROFILE_CACHE_TTL_SECONDS = 3600.0
_GITHUB_LOGIN_MAX_CHARS = 39
_GITHUB_LOGIN_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")

OPEN_SWE_BOT_NAME = "open-swe[bot]"
# Use the open-swe user noreply address: the bot's numeric noreply
# (215916821+open-swe[bot]@...) doesn't resolve to a GitHub account Vercel
# accepts, which broke preview deploys on commits carrying this co-author.
OPEN_SWE_BOT_EMAIL = "open-swe@users.noreply.github.com"

PR_ATTRIBUTION_TEXT = "Made by [Open SWE]"
PR_ATTRIBUTION_DEFAULT_URL = "https://github.com/langchain-ai/open-swe"
PR_ATTRIBUTION_FOOTER = f"{PR_ATTRIBUTION_TEXT}({PR_ATTRIBUTION_DEFAULT_URL})"


def build_pr_attribution_footer(
    thread_url: str | None = None,
    *,
    model_id: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """Build the Open SWE PR footer with the run's model details."""
    url = thread_url.strip() if isinstance(thread_url, str) and thread_url.strip() else ""
    footer = PR_ATTRIBUTION_FOOTER
    if url:
        footer += f" · [view thread]({url})"
    model = _normalize_text(model_id).replace("`", "")
    effort = _normalize_text(reasoning_effort).replace("`", "")
    if model:
        footer += f" · {model}"
        if effort:
            footer += f" ({effort})"
    return footer


@dataclass(frozen=True)
class CollaboratorIdentity:
    """Identity used for git trailers, PR attribution, and analytics."""

    display_name: str
    commit_name: str
    commit_email: str
    github_login: str = ""
    github_profile: bool = False
    github_user_id: int | None = None
    display_name_source: DisplayNameSource | None = None
    analytics_display_name: str = ""

    @property
    def pr_attribution_name(self) -> str:
        """Display name with GitHub login when available."""
        if self.github_login and self.github_login != self.display_name:
            return f"{self.display_name} (@{self.github_login})"
        return self.display_name


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


async def _identity_from_github_token(github_token: str | None) -> CollaboratorIdentity | None:
    if not github_token:
        return None

    try:
        async with httpx2.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
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
            github_login=login,
            github_profile=True,
            github_user_id=_positive_int(payload.get("id")),
            display_name_source="github",
            analytics_display_name=display_name,
        )
    except httpx2.HTTPError:
        logger.debug("Failed to resolve GitHub user identity from token", exc_info=True)
        return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value > 0 else None


def _is_valid_github_login(login: str) -> bool:
    return (
        0 < len(login) <= _GITHUB_LOGIN_MAX_CHARS
        and not login.startswith("-")
        and not login.endswith("-")
        and all(char in _GITHUB_LOGIN_ALLOWED for char in login.lower())
    )


@dataclass(frozen=True)
class GitHubPublicProfile:
    """Public GitHub profile for a login, fetched without user OAuth."""

    user_id: int
    name: str


async def _fetch_public_github_profile(login: str) -> GitHubPublicProfile | None:
    from agent.github.app import get_github_app_installation_token

    token = await get_github_app_installation_token(log_errors=False)
    if not token:
        return None
    try:
        async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.get(
                f"https://api.github.com/users/{login}",
                headers={
                    "Authorization": f"Bearer {token}",
                    **_GITHUB_API_HEADERS,
                },
            )
        if response.status_code != 200:  # noqa: PLR2004
            logger.debug(
                "GitHub public profile lookup for %s returned %s", login, response.status_code
            )
            return None
        payload = response.json()
    except httpx2.HTTPError, ValueError:
        logger.debug("Failed to resolve GitHub public profile for %s", login, exc_info=True)
        return None
    user_id = _positive_int(payload.get("id"))
    if user_id is None or _normalize_text(payload.get("login")).lower() != login.lower():
        return None
    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        return None
    return GitHubPublicProfile(user_id=user_id, name=_normalize_text(name))


async def resolve_public_github_profile(login: str) -> GitHubPublicProfile | None:
    """Cached ``GET /users/{login}`` via the installation token; misses never raise.

    Payloads are trusted only after strict validation: a positive numeric id, a
    ``login`` echoing the request (case-insensitively), and a ``name`` that is a
    string or null. Anything else is treated as a miss.
    """
    normalized = login.strip()
    if not _is_valid_github_login(normalized):
        return None

    async def _load() -> GitHubPublicProfile | None:
        return await _fetch_public_github_profile(normalized)

    try:
        return await ttl_cache.cached(
            f"github-public-profile:{normalized.lower()}",
            _PUBLIC_PROFILE_CACHE_TTL_SECONDS,
            _load,
        )
    except Exception:
        logger.debug("Failed to resolve GitHub public profile for %s", normalized, exc_info=True)
        return None


async def _identity_from_config(config: dict[str, Any]) -> CollaboratorIdentity | None:
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread", {})
    linear_issue = configurable.get("linear_issue", {})

    display_name = (
        _normalize_text(slack_thread.get("triggering_user_name"))
        or _normalize_text(linear_issue.get("triggering_user_name"))
        or _normalize_text(configurable.get("user_email")).split("@", 1)[0]
    )

    github_login = _normalize_text(configurable.get("github_login"))
    if github_login:
        github_user_id = configurable.get("github_user_id")
        commit_email = _github_noreply_email(github_login, github_user_id) or _normalize_text(
            await User.email_for_login(github_login)
        )
        if commit_email:
            commit_name = display_name or github_login
            github_profile = await resolve_public_github_profile(github_login)
            if github_profile is not None and github_profile.name:
                analytics_display_name = github_profile.name
                display_name_source: DisplayNameSource | None = "github"
            elif slack_thread.get("triggering_user_name"):
                # Slack names are only trusted server-side; the webhook fetches
                # them via users.info, never from the event payload.
                analytics_display_name = display_name
                display_name_source = "slack" if display_name else None
            else:
                analytics_display_name = ""
                display_name_source = None
            return CollaboratorIdentity(
                display_name=commit_name,
                commit_name=commit_name,
                commit_email=commit_email,
                github_login=github_login,
                github_user_id=(
                    github_profile.user_id
                    if github_profile is not None
                    else _positive_int(github_user_id)
                ),
                display_name_source=display_name_source,
                analytics_display_name=analytics_display_name,
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


async def resolve_triggering_user_identity(
    config: dict[str, Any],
    github_token: str | None = None,
) -> CollaboratorIdentity | None:
    """Resolve the triggering user's git identity.

    Prefer the GitHub account identity derived from the token when available.
    Fall back to config metadata when the run originated from GitHub or when
    Slack/Linear supplied an explicit user name and email.
    """

    return await _identity_from_github_token(github_token) or await _identity_from_config(config)


async def resolve_participant_identities(logins: Iterable[str]) -> list[CollaboratorIdentity]:
    """Git identities for thread participants the agent may author commits as."""
    unique = sorted({login.strip() for login in logins if isinstance(login, str) and login.strip()})
    emails = await asyncio.gather(*(User.email_for_login(login) for login in unique))
    identities: list[CollaboratorIdentity] = []
    for login, email in zip(unique, emails, strict=True):
        commit_email = _github_noreply_email(login) or _normalize_text(email)
        if not commit_email:
            continue
        identities.append(
            CollaboratorIdentity(
                display_name=login,
                commit_name=login,
                commit_email=commit_email,
                github_login=login,
            )
        )
    return identities


def add_bot_coauthor_trailer(commit_message: str) -> str:
    """Append the open-swe[bot] Co-authored-by trailer.

    Commits are authored by the triggering user (via the repo-local git
    identity); open-swe[bot] is credited as the collaborator.
    """
    normalized_message = commit_message.rstrip()
    trailer = f"Co-authored-by: {OPEN_SWE_BOT_NAME} <{OPEN_SWE_BOT_EMAIL}>"
    if trailer in normalized_message:
        return normalized_message
    return f"{normalized_message}\n\n{trailer}"


def add_pr_collaboration_note(
    pr_body: str,
    identity: CollaboratorIdentity | None = None,
    thread_url: str | None = None,
) -> str:
    """Append the Open SWE attribution footer to a PR body.

    The PR is opened as the triggering user, so the body only credits Open SWE
    as the collaborator. The footer links the run's thread when available. Any
    legacy double-attribution footer is replaced.
    """

    normalized_body = pr_body.rstrip()
    note = build_pr_attribution_footer(thread_url)
    if note in normalized_body:
        return normalized_body
    if PR_ATTRIBUTION_TEXT in normalized_body:
        return normalized_body

    legacy_footers: list[str] = []
    if identity is not None:
        legacy_footers.append(
            f"_Opened collaboratively by {identity.pr_attribution_name} and open-swe._"
        )
        legacy_footers.append(f"_Opened collaboratively by {identity.display_name} and open-swe._")
    for legacy in legacy_footers:
        if legacy in normalized_body:
            return normalized_body.replace(legacy, note)

    if not normalized_body:
        return note
    return f"{normalized_body}\n\n{note}"
