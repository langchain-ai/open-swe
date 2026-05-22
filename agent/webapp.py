"""Custom FastAPI routes for LangGraph server."""

import hashlib
import hmac
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages.content import create_text_block
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from .dashboard import router as dashboard_router
from .dashboard.agent_overrides import (
    get_profile_default_repo,
    resolve_login_from_email,
)
from .dashboard.enabled_repos import is_review_repo_enabled
from .dashboard.profiles import get_profile
from .dashboard.team_settings import get_team_settings
from .reviewer_findings import (
    REVIEWER_THREAD_KIND,
    ReviewerPRMeta,
    ReviewerSlackThread,
    set_reviewer_thread_metadata,
)
from .utils.auth import (
    is_bot_token_only_mode,
    persist_encrypted_github_token,
    resolve_github_token_from_email,
)
from .utils.authorship import OPEN_SWE_BOT_NAME
from .utils.comments import get_recent_comments
from .utils.github_app import (
    get_github_app_installation_token,
    get_github_app_installation_token_with_expiry,
)
from .utils.github_comments import (
    OPEN_SWE_TAGS,
    GitHubAuthError,
    build_pr_prompt,
    extract_pr_context,
    fetch_issue_comments,
    fetch_pr_comments_since_last_tag,
    format_github_comment_body_for_prompt,
    get_thread_id_from_branch,
    parse_github_review_command,
    react_to_github_comment,
    sanitize_github_comment_body,
    verify_github_signature,
)
from .utils.github_org_membership import INTERNAL_BOT_LOGINS, is_user_active_org_member
from .utils.github_token import get_github_token_from_thread, invalidate_cached_github_token
from .utils.github_user_email_map import GITHUB_USER_EMAIL_MAP
from .utils.linear import post_linear_trace_comment
from .utils.linear_team_repo_map import LINEAR_TEAM_TO_REPO
from .utils.multimodal import dedupe_urls, extract_image_urls, fetch_image_block
from .utils.repo import extract_repo_from_text
from .utils.sandbox import validate_sandbox_startup_config
from .utils.slack import (
    GitHubPrRef,
    fetch_slack_thread_messages,
    format_slack_messages_for_prompt,
    get_slack_user_info,
    get_slack_user_names,
    looks_like_slack_pr_review_command,
    parse_github_pr_url,
    parse_slack_review_command,
    post_slack_thread_reply,
    post_slack_trace_reply,
    resolve_slack_links_in_context,
    select_slack_context_messages,
    set_slack_assistant_status,
    store_slack_run_mapping,
    strip_bot_mention,
    verify_slack_signature,
)
from .utils.slack_feedback import (
    FEEDBACK_REACTIONS,
    process_slack_reaction_added,
    process_slack_reaction_removed,
)
from .utils.thread_ops import is_thread_active, queue_message_for_thread

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_sandbox_startup_config()
    yield


app = FastAPI(lifespan=lifespan)

DASHBOARD_ALLOWED_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
if DASHBOARD_ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DASHBOARD_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

app.include_router(dashboard_router)

LINEAR_WEBHOOK_SECRET = os.environ.get("LINEAR_WEBHOOK_SECRET", "")
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_USER_ID = os.environ.get("SLACK_BOT_USER_ID", "")
SLACK_BOT_USERNAME = os.environ.get("SLACK_BOT_USERNAME", "")
DEFAULT_REPO_OWNER = os.environ.get("DEFAULT_REPO_OWNER", "langchain-ai")
DEFAULT_REPO_NAME = os.environ.get("DEFAULT_REPO_NAME", "langchainplus")
SLACK_REPO_OWNER = os.environ.get("SLACK_REPO_OWNER", "") or DEFAULT_REPO_OWNER
SLACK_REPO_NAME = os.environ.get("SLACK_REPO_NAME", "") or DEFAULT_REPO_NAME

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

_AGENT_VERSION_METADATA: dict[str, str] = (
    {"LANGSMITH_AGENT_VERSION": os.environ["LANGCHAIN_REVISION_ID"]}
    if os.environ.get("LANGCHAIN_REVISION_ID")
    else {}
)

ALLOWED_GITHUB_ORGS: frozenset[str] = frozenset(
    org.strip().lower()
    for org in os.environ.get("ALLOWED_GITHUB_ORGS", "").split(",")
    if org.strip()
)
ALLOWED_REVIEWER_GITHUB_ORGS: frozenset[str] = frozenset(
    org.strip().lower()
    for org in os.environ.get("ALLOWED_REVIEWER_GITHUB_ORGS", "").split(",")
    if org.strip()
)
ALLOWED_REVIEWER_GITHUB_REPOS: frozenset[str] = frozenset(
    repo.strip().lower()
    for repo in os.environ.get("ALLOWED_REVIEWER_GITHUB_REPOS", "").split(",")
    if repo.strip()
)
# Org whose members are allowed to tag @open-swe on public repos. When empty,
# the public-repo gate is disabled (back-compat).
PUBLIC_REPO_ORG_GATE: str = os.environ.get("PUBLIC_REPO_ORG_GATE", "").strip()

ALLOWED_GITHUB_REPOS: frozenset[str] = frozenset(
    repo.strip().lower()
    for repo in os.environ.get("ALLOWED_GITHUB_REPOS", "").split(",")
    if repo.strip()
)

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")

_GITHUB_BOT_MESSAGE_PREFIXES = (
    "🔐 **GitHub Authentication Required**",
    "✅ **Pull Request Created**",
    "✅ **Pull Request Updated**",
    "**Pull Request Created**",
    "**Pull Request Updated**",
    "🤖 **Agent Response**",
    "❌ **Agent Error**",
)


def get_repo_config_from_team_mapping(
    team_identifier: str, project_name: str = ""
) -> dict[str, str]:
    """Look up repository configuration from LINEAR_TEAM_TO_REPO mapping."""
    fallback = {"owner": DEFAULT_REPO_OWNER, "name": DEFAULT_REPO_NAME}

    if not team_identifier or team_identifier not in LINEAR_TEAM_TO_REPO:
        return fallback

    config = LINEAR_TEAM_TO_REPO[team_identifier]

    if "owner" in config and "name" in config:
        return config

    if "projects" in config and project_name:
        project_config = config["projects"].get(project_name)
        if project_config:
            return project_config

    if "default" in config:
        return config["default"]

    return fallback


async def react_to_linear_comment(comment_id: str, emoji: str = "👀") -> bool:
    """Add an emoji reaction to a Linear comment.

    Args:
        comment_id: The Linear comment ID
        emoji: The emoji to react with (default: eyes 👀)

    Returns:
        True if successful, False otherwise
    """
    if not LINEAR_API_KEY:
        return False

    url = "https://api.linear.app/graphql"

    mutation = """
    mutation ReactionCreate($commentId: String!, $emoji: String!) {
        reactionCreate(input: { commentId: $commentId, emoji: $emoji }) {
            success
        }
    }
    """

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers={
                    "Authorization": LINEAR_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "query": mutation,
                    "variables": {"commentId": comment_id, "emoji": emoji},
                },
            )
            response.raise_for_status()
            result = response.json()
            return bool(result.get("data", {}).get("reactionCreate", {}).get("success"))
        except Exception:  # noqa: BLE001
            return False


async def fetch_linear_issue_details(issue_id: str) -> dict[str, Any] | None:
    """Fetch full issue details from Linear API including description and comments.

    Args:
        issue_id: The Linear issue ID

    Returns:
        Full issue data dict, or None if fetch failed
    """
    if not LINEAR_API_KEY:
        return None

    url = "https://api.linear.app/graphql"

    query = """
    query GetIssue($issueId: String!) {
        issue(id: $issueId) {
            id
            identifier
            title
            description
            url
            project {
                id
                name
            }
            team {
                id
                name
                key
            }
            comments {
                nodes {
                    id
                    body
                    createdAt
                    user {
                        id
                        name
                        email
                    }
                }
            }
        }
    }
    """

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers={
                    "Authorization": LINEAR_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "variables": {"issueId": issue_id},
                },
            )
            response.raise_for_status()
            result = response.json()

            return result.get("data", {}).get("issue")
        except httpx.HTTPError:
            return None


def generate_thread_id_from_issue(issue_id: str) -> str:
    """Generate a deterministic thread ID from a Linear issue ID.

    Args:
        issue_id: The Linear issue ID

    Returns:
        A UUID-formatted thread ID derived from the issue ID
    """
    hash_bytes = hashlib.sha256(f"linear-issue:{issue_id}".encode()).hexdigest()
    return (
        f"{hash_bytes[:8]}-{hash_bytes[8:12]}-{hash_bytes[12:16]}-"
        f"{hash_bytes[16:20]}-{hash_bytes[20:32]}"
    )


def generate_thread_id_from_github_issue(issue_id: str) -> str:
    """Generate a deterministic thread ID from a GitHub issue ID."""
    hash_bytes = hashlib.sha256(f"github-issue:{issue_id}".encode()).hexdigest()
    return (
        f"{hash_bytes[:8]}-{hash_bytes[8:12]}-{hash_bytes[12:16]}-"
        f"{hash_bytes[16:20]}-{hash_bytes[20:32]}"
    )


def generate_thread_id_from_slack_thread(channel_id: str, thread_id: str) -> str:
    """Generate a deterministic thread ID from a Slack thread identifier."""
    composite = f"{channel_id}:{thread_id}"
    md5_hex = hashlib.md5(composite.encode("utf-8")).hexdigest()
    return str(uuid.UUID(hex=md5_hex))


def generate_reviewer_thread_id(owner: str, repo: str, pr_number: int) -> str:
    stable_key = f"{owner}/{repo}/pr/{pr_number}/reviewer"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key))


def _extract_repo_config_from_thread(thread: dict[str, Any]) -> dict[str, str] | None:
    """Extract repo config from persisted thread data."""
    metadata = thread.get("metadata")
    if not isinstance(metadata, dict):
        return None

    repo = metadata.get("repo")
    if isinstance(repo, dict):
        owner = repo.get("owner")
        name = repo.get("name")
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            return {"owner": owner, "name": name}

    owner = metadata.get("repo_owner")
    name = metadata.get("repo_name")
    if isinstance(owner, str) and owner and isinstance(name, str) and name:
        return {"owner": owner, "name": name}

    return None


def _is_not_found_error(exc: Exception) -> bool:
    """Best-effort check for LangGraph 404 errors."""
    return getattr(exc, "status_code", None) == 404


def _run_id_for_logging(run: Any) -> str:
    """Extract a run id from SDK response shapes for log messages."""
    if isinstance(run, dict):
        run_id = run.get("run_id")
    else:
        run_id = getattr(run, "run_id", None)
    return run_id if isinstance(run_id, str) and run_id else "<unknown>"


def _is_repo_allowed(repo_config: dict[str, str]) -> bool:
    """Check if the repo is in the allowlist.

    Returns True if no allowlist is configured (both ALLOWED_GITHUB_ORGS and
    ALLOWED_GITHUB_REPOS are empty), or if the repo owner is in
    ALLOWED_GITHUB_ORGS, or if owner/name is in ALLOWED_GITHUB_REPOS.
    """
    if not ALLOWED_GITHUB_ORGS and not ALLOWED_GITHUB_REPOS:
        return True
    owner = repo_config.get("owner", "").lower()
    name = repo_config.get("name", "").lower()
    if ALLOWED_GITHUB_ORGS and owner in ALLOWED_GITHUB_ORGS:
        return True
    if ALLOWED_GITHUB_REPOS and f"{owner}/{name}" in ALLOWED_GITHUB_REPOS:
        return True
    return False


def _is_repo_allowed_for_reviewer(repo_config: dict[str, str]) -> bool:
    """Check if a repo is allowed for reviewer-agent webhook entrypoints."""
    owner = repo_config.get("owner", "").lower()
    name = repo_config.get("name", "").lower()
    full_name = f"{owner}/{name}" if owner and name else ""

    if ALLOWED_REVIEWER_GITHUB_REPOS:
        return full_name in ALLOWED_REVIEWER_GITHUB_REPOS

    if not ALLOWED_REVIEWER_GITHUB_ORGS:
        return True
    return owner in ALLOWED_REVIEWER_GITHUB_ORGS


async def _is_repo_enabled_for_review(repo_config: dict[str, str]) -> bool:
    """Combined gate: operator allowlist + team opt-in list from the dashboard.

    Both checks must pass. The opt-in list is empty by default, so repos
    are off until an admin enables them in the dashboard's Open SWE Review
    tab.
    """
    if not _is_repo_allowed_for_reviewer(repo_config):
        return False
    return await is_review_repo_enabled(repo_config.get("owner", ""), repo_config.get("name", ""))


_PUBLIC_REPO_GATE_REJECTION = {
    "status": "ignored",
    "reason": "Sender is not a member of the allowed organization for public-repo triggers",
}


async def _is_sender_allowed_for_public_repo(payload: dict[str, Any]) -> bool:
    """Public-repo gate: only ``PUBLIC_REPO_ORG_GATE`` org members may trigger.

    Returns True (allowed) when:
    - The gate is disabled (``PUBLIC_REPO_ORG_GATE`` empty), OR
    - The repo is private (gate only applies to public repos), OR
    - The sender is a known internal bot, OR
    - The sender is an active member of ``PUBLIC_REPO_ORG_GATE``.
    """
    if not PUBLIC_REPO_ORG_GATE:
        return True

    repository = payload.get("repository") or {}
    if repository.get("private", False):
        return True

    sender = payload.get("sender") or {}
    sender_login = sender.get("login", "") or ""
    if sender_login in INTERNAL_BOT_LOGINS:
        return True

    if not sender_login:
        return False

    return await is_user_active_org_member(sender_login, PUBLIC_REPO_ORG_GATE)


async def _enforce_public_repo_org_gate(
    payload: dict[str, Any], event_type: str
) -> dict[str, str] | None:
    """Return a rejection response if the public-repo org gate blocks this event."""
    if await _is_sender_allowed_for_public_repo(payload):
        return None
    sender_login = (payload.get("sender") or {}).get("login", "")
    repo = payload.get("repository") or {}
    logger.warning(
        "Blocking GitHub %s from non-org-member sender '%s' on public repo '%s/%s'",
        event_type,
        sender_login,
        (repo.get("owner") or {}).get("login", ""),
        repo.get("name", ""),
    )
    return _PUBLIC_REPO_GATE_REJECTION


async def _upsert_slack_thread_repo_metadata(
    thread_id: str, repo_config: dict[str, str], langgraph_client: LangGraphClient
) -> None:
    """Persist the selected repo config on the thread metadata."""
    try:
        await langgraph_client.threads.update(thread_id=thread_id, metadata={"repo": repo_config})
    except Exception as exc:  # noqa: BLE001
        if _is_not_found_error(exc):
            try:
                await langgraph_client.threads.create(
                    thread_id=thread_id,
                    if_exists="do_nothing",
                    metadata={"repo": repo_config},
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to create Slack thread %s while persisting repo metadata",
                    thread_id,
                )
            return
        logger.exception(
            "Failed to persist Slack thread repo metadata for thread %s",
            thread_id,
        )


async def get_slack_repo_config(
    channel_id: str,
    thread_ts: str,
    slack_user_id: str | None = None,
) -> dict[str, str]:
    """Resolve repository configuration for Slack-triggered runs.

    Priority:
        1. Repo carried over from the existing Slack thread's metadata.
        2. The triggering user's dashboard ``default_repo`` (if they have a
           profile and their Slack email maps to a known GitHub login).
        3. ``SLACK_REPO_*`` env defaults.
    """
    default_owner = SLACK_REPO_OWNER.strip() or DEFAULT_REPO_OWNER
    default_name = SLACK_REPO_NAME.strip() or DEFAULT_REPO_NAME
    thread_id = generate_thread_id_from_slack_thread(channel_id, thread_ts)
    langgraph_client = get_client(url=LANGGRAPH_URL)

    repo_config: dict[str, str] | None = None

    try:
        thread = await langgraph_client.threads.get(thread_id)
        thread_repo_config = _extract_repo_config_from_thread(thread)
        if thread_repo_config:
            repo_config = thread_repo_config
    except Exception as exc:  # noqa: BLE001
        if not _is_not_found_error(exc):
            logger.exception(
                "Failed to fetch Slack thread %s for repo resolution",
                thread_id,
            )

    if not repo_config and slack_user_id:
        try:
            slack_user = await get_slack_user_info(slack_user_id)
            slack_email = (
                (slack_user or {}).get("profile", {}).get("email")
                if isinstance(slack_user, dict)
                else None
            )
            profile_repo = await get_profile_default_repo(resolve_login_from_email(slack_email))
            if profile_repo:
                logger.info(
                    "Applying dashboard default_repo for Slack user %s: %s/%s",
                    slack_user_id,
                    profile_repo["owner"],
                    profile_repo["name"],
                )
                repo_config = profile_repo
        except Exception:  # noqa: BLE001
            logger.exception("Failed to apply dashboard default_repo for Slack user")

    if not repo_config:
        repo_config = {"owner": default_owner, "name": default_name}

    return repo_config


async def _thread_exists(thread_id: str) -> bool:
    """Return whether a LangGraph thread already exists."""
    langgraph_client = get_client(url=LANGGRAPH_URL)
    try:
        await langgraph_client.threads.get(thread_id)
        return True
    except Exception as exc:  # noqa: BLE001
        if _is_not_found_error(exc):
            return False
        logger.warning("Failed to fetch thread %s, assuming it exists", thread_id)
        return True


async def _ensure_thread_exists_for_metadata(
    thread_id: str, langgraph_client: LangGraphClient
) -> bool:
    try:
        await langgraph_client.threads.create(thread_id=thread_id, if_exists="do_nothing")
        return True
    except Exception:
        logger.exception("Failed to ensure thread %s exists before metadata update", thread_id)
        return False


async def process_linear_issue(  # noqa: PLR0912, PLR0915
    issue_data: dict[str, Any], repo_config: dict[str, str]
) -> None:
    """Process a Linear issue by creating a new LangGraph thread and run.

    Args:
        issue_data: The Linear issue data from webhook (basic info only).
        repo_config: The repo configuration with owner and name.
    """
    issue_id = issue_data.get("id", "")
    logger.info(
        "Processing Linear issue %s for repo %s/%s",
        issue_id,
        repo_config.get("owner"),
        repo_config.get("name"),
    )

    triggering_comment_id = issue_data.get("triggering_comment_id", "")
    if triggering_comment_id:
        await react_to_linear_comment(triggering_comment_id, "👀")

    thread_id = generate_thread_id_from_issue(issue_id)

    full_issue = await fetch_linear_issue_details(issue_id)
    if not full_issue:
        full_issue = issue_data

    user_email = None
    user_name = None
    comment_author = issue_data.get("comment_author", {})
    if comment_author:
        user_email = comment_author.get("email")
        user_name = comment_author.get("name")
    if not user_email:
        creator = full_issue.get("creator", {})
        if creator:
            user_email = creator.get("email")
            user_name = user_name or creator.get("name")
    if not user_email:
        assignee = full_issue.get("assignee", {})
        if assignee:
            user_email = assignee.get("email")
            user_name = user_name or assignee.get("name")

    logger.info("User email for issue %s: %s", issue_id, user_email)

    title = full_issue.get("title", "No title")
    description = full_issue.get("description") or "No description"
    image_urls: list[str] = []
    description_image_urls = extract_image_urls(description)
    if description_image_urls:
        image_urls.extend(description_image_urls)
        logger.debug(
            "Found %d image URL(s) in issue description",
            len(description_image_urls),
        )

    comments = full_issue.get("comments", {}).get("nodes", [])
    comments_text = ""
    triggering_comment = issue_data.get("triggering_comment", "")
    triggering_comment_id = issue_data.get("triggering_comment_id", "")

    bot_message_prefixes = (
        "🔐 **GitHub Authentication Required**",
        "✅ **Pull Request Created**",
        "✅ **Pull Request Updated**",
        "**Pull Request Created**",
        "**Pull Request Updated**",
        "🤖 **Agent Response**",
        "❌ **Agent Error**",
    )

    comment_ids: set[str] = set()
    comment_id_to_index: dict[str, int] = {}
    if comments:
        for i, comment in enumerate(comments):
            comment_id = comment.get("id", "")
            if comment_id:
                comment_ids.add(comment_id)
                comment_id_to_index[comment_id] = i

        relevant_comments = []
        trigger_index = None
        if triggering_comment_id:
            trigger_index = comment_id_to_index.get(triggering_comment_id)
        if trigger_index is not None:
            relevant_comments = comments[trigger_index:]
            logger.debug(
                "Using triggering comment index %d to build relevant comments",
                trigger_index,
            )
        else:
            relevant_comments = get_recent_comments(comments, bot_message_prefixes)

        if relevant_comments:
            comments_text = "\n\n## Comments:\n"
            for comment in relevant_comments:
                user = comment.get("user") or {}
                author = user.get("name", "User")
                body = comment.get("body", "")
                body_image_urls = extract_image_urls(body)
                if body_image_urls:
                    image_urls.extend(body_image_urls)
                    logger.debug(
                        "Found %d image URL(s) in comment by %s",
                        len(body_image_urls),
                        author,
                    )
                if any(body.startswith(prefix) for prefix in bot_message_prefixes):
                    continue
                comments_text += f"\n**{author}:** {body}\n"

    if triggering_comment and triggering_comment_id not in comment_ids:
        if not comments_text:
            comments_text = "\n\n## Comments:\n"
        trigger_author = comment_author.get("name", "Unknown")
        trigger_body = triggering_comment
        trigger_image_urls = extract_image_urls(trigger_body)
        if trigger_image_urls:
            image_urls.extend(trigger_image_urls)
            logger.debug(
                "Found %d image URL(s) in triggering comment by %s",
                len(trigger_image_urls),
                trigger_author,
            )
        comments_text += f"\n**{trigger_author}:** {trigger_body}\n"
        logger.debug(
            "Appended triggering comment %s not present in issue comments list",
            triggering_comment_id or "<missing-id>",
        )

    identifier = full_issue.get("identifier", "") or issue_data.get("identifier", "")

    triggered_by_line = f"## Triggered by: {user_name}\n\n" if user_name else ""
    tag_instruction = (
        f"When calling linear_comment, tag @{user_name} if you are asking them a question, need their input, or are notifying them of something important (e.g. a completed PR). For simple answers, tagging is not required."
        if user_name
        else ""
    )
    prompt = (
        f"Please work on the following issue:\n\n"
        f"## Title: {title}\n\n"
        f"{triggered_by_line}"
        f"## Linear Ticket: {identifier} - Ticket ID: {issue_id}\n\n"
        f"## Description:\n{description}\n"
        f"{comments_text}\n\n"
        f"Please analyze this issue and implement the necessary changes. "
        f"When you're done, commit and push your changes. {tag_instruction}"
    )
    content_blocks: list[dict[str, Any]] = [create_text_block(prompt)]
    if image_urls:
        image_urls = dedupe_urls(image_urls)
        logger.info("Preparing %d image(s) for multimodal content", len(image_urls))
        logger.debug("Image URLs: %s", image_urls)

        async with httpx.AsyncClient() as client:
            for image_url in image_urls:
                image_block = await fetch_image_block(image_url, client)
                if image_block:
                    content_blocks.append(image_block)
        logger.info("Built %d content block(s) for prompt", len(content_blocks))

    linear_project_id = ""
    linear_issue_number = ""
    if identifier and "-" in identifier:
        parts = identifier.split("-", 1)
        linear_project_id = parts[0]
        linear_issue_number = parts[1]

    configurable: dict[str, Any] = {
        "repo": repo_config,
        "linear_issue": {
            "id": issue_id,
            "title": title,
            "url": full_issue.get("url", "") or issue_data.get("url", ""),
            "identifier": identifier,
            "linear_project_id": linear_project_id,
            "linear_issue_number": linear_issue_number,
            "triggering_user_name": user_name or "",
        },
        "user_email": user_email,
        "source": "linear",
    }

    logger.info("Checking if thread %s is active before creating run", thread_id)
    thread_active = await is_thread_active(thread_id)
    logger.info("Thread %s active status: %s", thread_id, thread_active)

    if thread_active:
        logger.info(
            "Thread %s is active (busy), will queue message instead of creating run",
            thread_id,
        )

        queued_payload = {"text": prompt, "image_urls": image_urls}
        queued = await queue_message_for_thread(
            thread_id=thread_id,
            message_content=queued_payload,
        )

        if queued:
            logger.info("Message queued for thread %s, will be processed by middleware", thread_id)
            langgraph_client = get_client(url=LANGGRAPH_URL)
            runs = await langgraph_client.runs.list(thread_id, limit=1)
            if runs:
                await post_linear_trace_comment(issue_id, thread_id, triggering_comment_id)
        else:
            logger.error("Failed to queue message for thread %s", thread_id)
    else:
        logger.info("Creating LangGraph run for thread %s", thread_id)
        langgraph_client = get_client(url=LANGGRAPH_URL)
        await langgraph_client.runs.create(
            thread_id,
            "agent",
            input={"messages": [{"role": "user", "content": content_blocks}]},
            config={"configurable": configurable, "metadata": _AGENT_VERSION_METADATA},
            if_not_exists="create",
        )
        logger.info("LangGraph run created successfully for thread %s", thread_id)
        await post_linear_trace_comment(issue_id, thread_id, triggering_comment_id)


async def process_slack_mention(event_data: dict[str, Any], repo_config: dict[str, str]) -> None:
    """Process a Slack app mention by creating a run or queuing a mid-run message."""
    channel_id = event_data.get("channel_id", "")
    thread_ts = event_data.get("thread_ts", "")
    event_ts = event_data.get("event_ts", "")
    user_id = event_data.get("user_id", "")
    text = event_data.get("text", "")
    bot_user_id = event_data.get("bot_user_id", "")

    if not channel_id or not thread_ts or not event_ts:
        logger.warning(
            "Missing Slack event fields (channel_id=%s, thread_ts=%s, event_ts=%s)",
            channel_id,
            thread_ts,
            event_ts,
        )
        return

    await set_slack_assistant_status(channel_id, thread_ts)

    thread_id = generate_thread_id_from_slack_thread(channel_id, thread_ts)

    user_email = None
    user_name = ""
    if user_id:
        slack_user = await get_slack_user_info(user_id)
        if slack_user:
            profile = slack_user.get("profile", {})
            if isinstance(profile, dict):
                user_email = profile.get("email")
                user_name = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or slack_user.get("real_name")
                    or slack_user.get("name")
                    or ""
                )

    thread_messages = await fetch_slack_thread_messages(channel_id, thread_ts)
    if not any(str(message.get("ts")) == str(event_ts) for message in thread_messages):
        thread_messages.append({"ts": event_ts, "text": text, "user": user_id})

    context_messages, context_mode = select_slack_context_messages(
        thread_messages, event_ts, bot_user_id, SLACK_BOT_USERNAME
    )
    context_user_ids = [
        value
        for value in (message.get("user") for message in context_messages)
        if isinstance(value, str) and value
    ]
    user_names_by_id = await get_slack_user_names(context_user_ids)
    if user_id and user_name and user_id not in user_names_by_id:
        user_names_by_id[user_id] = user_name
    context_text = format_slack_messages_for_prompt(
        context_messages,
        user_names_by_id,
        bot_user_id=bot_user_id,
        bot_username=SLACK_BOT_USERNAME,
    )
    context_source = (
        "the previous message where I was tagged"
        if context_mode == "last_mention"
        else "the beginning of the thread"
    )
    clean_text = (
        strip_bot_mention(text, bot_user_id, bot_username=SLACK_BOT_USERNAME)
        or "(no text in mention)"
    )
    trigger_user = user_name or (f"<@{user_id}>" if user_id else "Unknown user")

    # Auto-resolve cross-posted Slack message links in context
    resolved_links_section, image_urls_from_links = await resolve_slack_links_in_context(
        context_messages, user_names_by_id
    )

    prompt = (
        "You were mentioned in Slack.\n\n"
        "## Default Repository Hint\n"
        f"{repo_config.get('owner')}/{repo_config.get('name')}\n"
        "Use this only if the Slack conversation does not identify a different repository.\n\n"
        f"## Triggered by\n{trigger_user}\n\n"
        f"## Slack Thread\n- Channel: {channel_id}\n- Thread TS: {thread_ts}\n"
        f"- Context starts at: {context_source}\n\n"
        f"## Conversation Context\n{context_text}\n\n"
        f"## Latest Mention Request\n{clean_text}\n\n"
        + (f"{resolved_links_section}\n\n" if resolved_links_section else "")
        + "Use `slack_thread_reply` to communicate in this Slack thread for clarifications, "
        "status updates, and final summaries. Use `slack_read_thread_messages` to read any "
        "Slack messages by providing channel_id and message_ts."
    )
    content_blocks: list[dict[str, Any]] = [create_text_block(prompt)]

    image_urls = dedupe_urls(
        [url for msg in context_messages for url in extract_image_urls(msg.get("text", ""))]
        + [
            f["url_private"]
            for msg in context_messages
            for f in msg.get("files", [])
            if isinstance(f, dict)
            and f.get("mimetype", "").startswith("image/")
            and f.get("url_private")
        ]
        + image_urls_from_links
    )
    if image_urls:
        logger.info("Preparing %d image(s) for Slack mention", len(image_urls))
        async with httpx.AsyncClient() as http_client:
            for image_url in image_urls:
                image_block = await fetch_image_block(image_url, http_client)
                if image_block:
                    content_blocks.append(image_block)

    configurable: dict[str, Any] = {
        "repo": repo_config,
        "slack_thread": {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "triggering_user_id": user_id,
            "triggering_user_name": user_name,
            "triggering_user_email": user_email,
            "triggering_event_ts": event_ts,
        },
        "user_email": user_email,
        "source": "slack",
    }

    langgraph_client = get_client(url=LANGGRAPH_URL)
    is_first_mention = not await _thread_exists(thread_id)
    await _upsert_slack_thread_repo_metadata(thread_id, repo_config, langgraph_client)

    thread_active = await is_thread_active(thread_id)
    if thread_active:
        logger.info(
            "Thread %s is active, queuing Slack message for middleware pickup",
            thread_id,
        )
        queued_payload = {"text": prompt, "image_urls": image_urls}
        queued = await queue_message_for_thread(
            thread_id=thread_id,
            message_content=queued_payload,
        )
        if queued:
            logger.info("Slack message queued for thread %s", thread_id)
        else:
            logger.error("Failed to queue Slack message for thread %s", thread_id)
        return

    logger.info("Creating Slack LangGraph run for thread %s", thread_id)
    run = await langgraph_client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": content_blocks}]},
        config={"configurable": configurable, "metadata": _AGENT_VERSION_METADATA},
        if_not_exists="create",
    )
    logger.info(
        "Slack LangGraph run %s created for thread %s",
        _run_id_for_logging(run),
        thread_id,
    )
    run_id = run.get("run_id")
    if is_first_mention:
        trace_message_ts = await post_slack_trace_reply(channel_id, thread_ts, thread_id)
        await set_slack_assistant_status(channel_id, thread_ts)
        if isinstance(run_id, str) and run_id:
            await store_slack_run_mapping(
                langgraph_client,
                channel_id,
                thread_ts,
                run_id,
                message_ts=trace_message_ts,
                triggering_user_id=user_id,
            )
    else:
        logger.info(
            "Skipping Slack trace reply for thread %s — agent will reply when run completes",
            thread_id,
        )
        if isinstance(run_id, str) and run_id:
            await store_slack_run_mapping(
                langgraph_client,
                channel_id,
                thread_ts,
                run_id,
                triggering_user_id=user_id,
            )


async def process_slack_pr_review_request(
    pr_ref: GitHubPrRef, channel_id: str, thread_ts: str
) -> None:
    await set_slack_assistant_status(channel_id, thread_ts)
    result = await trigger_pr_review_from_ref(
        pr_ref,
        source="slack",
        slack_channel_id=channel_id,
        slack_thread_ts=thread_ts,
    )
    if result.get("success"):
        thread_id = result.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            await post_slack_trace_reply(channel_id, thread_ts, thread_id)
            await set_slack_assistant_status(channel_id, thread_ts)
        return

    await post_slack_thread_reply(
        channel_id,
        thread_ts,
        f"Could not start review for <{pr_ref.url}|{pr_ref.owner}/{pr_ref.repo}#{pr_ref.number}>: "
        f"{result.get('error', 'unknown error')}.",
    )


def verify_linear_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify the Linear webhook signature.

    Args:
        body: Raw request body bytes
        signature: The Linear-Signature header value
        secret: The webhook signing secret

    Returns:
        True if signature is valid, False otherwise
    """
    if not secret:
        logger.warning("LINEAR_WEBHOOK_SECRET is not configured — rejecting webhook request")
        return False

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected, signature)


@app.post("/webhooks/linear")
async def linear_webhook(  # noqa: PLR0911, PLR0912, PLR0915
    request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Handle Linear webhooks.

    Triggers a new LangGraph run when an issue gets the 'open-swe' label added.
    """
    logger.info("Received Linear webhook")
    body = await request.body()

    signature = request.headers.get("Linear-Signature", "")
    if not verify_linear_signature(body, signature, LINEAR_WEBHOOK_SECRET):
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    if payload.get("type") != "Comment":
        logger.debug("Ignoring webhook: not a Comment event")
        return {"status": "ignored", "reason": "Not a Comment event"}

    action = payload.get("action")
    if action != "create":
        logger.debug("Ignoring webhook: action is %s, not create", action)
        return {
            "status": "ignored",
            "reason": f"Comment action is '{action}', only processing 'create'",
        }

    data = payload.get("data", {})

    if data.get("botActor"):
        logger.debug("Ignoring webhook: comment is from a bot")
        return {"status": "ignored", "reason": "Comment is from a bot"}

    comment_body = data.get("body", "")
    bot_message_prefixes = [
        "🔐 **GitHub Authentication Required**",
        "✅ **Pull Request Created**",
        "✅ **Pull Request Updated**",
        "**Pull Request Created**",
        "**Pull Request Updated**",
        "🤖 **Agent Response**",
        "❌ **Agent Error**",
    ]
    for prefix in bot_message_prefixes:
        if comment_body.startswith(prefix):
            logger.debug("Ignoring webhook: comment is our own bot message")
            return {"status": "ignored", "reason": "Comment is our own bot message"}
    if "@openswe" not in comment_body.lower():
        logger.debug("Ignoring webhook: comment doesn't mention @openswe")
        return {"status": "ignored", "reason": "Comment doesn't mention @openswe"}

    issue = data.get("issue", {})
    if not issue:
        logger.debug("Ignoring webhook: no issue data in comment")
        return {"status": "ignored", "reason": "No issue data in comment"}

    # Fetch full issue details to get project info (webhook doesn't include it)
    issue_id = issue.get("id", "")
    full_issue = await fetch_linear_issue_details(issue_id)
    if not full_issue:
        logger.warning("Failed to fetch full issue details, using webhook data")
        full_issue = issue

    repo_config = extract_repo_from_text(comment_body, default_owner=DEFAULT_REPO_OWNER)

    if repo_config:
        logger.debug(
            "Using repo from comment body: %s/%s",
            repo_config["owner"],
            repo_config["name"],
        )
    else:
        comment_user_email = (data.get("user") or {}).get("email")
        try:
            profile_repo = await get_profile_default_repo(
                resolve_login_from_email(comment_user_email)
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to apply dashboard default_repo for Linear user")
            profile_repo = None
        if profile_repo:
            logger.info(
                "Applying dashboard default_repo for Linear user %s: %s/%s",
                comment_user_email,
                profile_repo["owner"],
                profile_repo["name"],
            )
            repo_config = profile_repo

    if not repo_config:
        team = full_issue.get("team", {})
        team_name = team.get("name", "") if team else ""
        project = full_issue.get("project")
        project_name = project.get("name", "") if project else ""

        team_identifier = team_name.strip() if team_name else ""
        project_key = project_name.strip() if project_name else ""

        repo_config = get_repo_config_from_team_mapping(team_identifier, project_key)

        logger.debug(
            "Team/project lookup result",
            extra={
                "team_name": team_identifier,
                "project_name": project_key,
                "repo_config": repo_config,
            },
        )

    if not _is_repo_allowed(repo_config):
        logger.warning(
            "Rejecting Linear webhook: repo '%s/%s' not in allowlist",
            repo_config.get("owner"),
            repo_config.get("name"),
        )
        return {"status": "ignored", "reason": "Repository not in allowlist"}

    repo_owner = repo_config["owner"]
    repo_name = repo_config["name"]

    issue["triggering_comment"] = comment_body
    issue["triggering_comment_id"] = data.get("id", "")
    comment_user = data.get("user", {})
    if comment_user:
        issue["comment_author"] = comment_user

    logger.info(
        "Accepted webhook for issue '%s' (%s), scheduling background task",
        issue.get("title"),
        issue.get("id"),
    )
    background_tasks.add_task(process_linear_issue, issue, repo_config)

    return {
        "status": "accepted",
        "message": f"Processing issue '{issue.get('title')}' for repo {repo_owner}/{repo_name}",
    }


@app.get("/webhooks/linear")
async def linear_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Linear webhook setup."""
    return {"status": "ok", "message": "Linear webhook endpoint is active"}


@app.post("/webhooks/slack")
async def slack_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Handle Slack Event API webhooks for app mentions."""
    body = await request.body()

    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if not verify_slack_signature(
        body=body,
        timestamp=timestamp,
        signature=signature,
        secret=SLACK_SIGNING_SECRET,
    ):
        logger.warning("Invalid Slack signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse Slack webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge", "")
        return {"challenge": challenge}

    if payload.get("type") != "event_callback":
        return {"status": "ignored", "reason": "Not an event callback"}

    event = payload.get("event", {})

    if event.get("type") == "reaction_added":
        reaction = event.get("reaction")
        if reaction in FEEDBACK_REACTIONS:
            background_tasks.add_task(
                process_slack_reaction_added, event, payload.get("event_id", "")
            )
            return {"status": "accepted", "message": "Reaction feedback queued"}
        return {"status": "ignored", "reason": "Reaction not tracked for feedback"}

    if event.get("type") == "reaction_removed":
        reaction = event.get("reaction")
        if reaction in FEEDBACK_REACTIONS:
            background_tasks.add_task(
                process_slack_reaction_removed, event, payload.get("event_id", "")
            )
            return {"status": "accepted", "message": "Reaction removal queued"}
        return {"status": "ignored", "reason": "Reaction not tracked for feedback"}

    if event.get("type") != "app_mention":
        message_text = event.get("text", "")
        has_username_mention = bool(
            event.get("type") == "message"
            and SLACK_BOT_USERNAME
            and f"@{SLACK_BOT_USERNAME}" in message_text
        )
        has_id_mention = bool(
            event.get("type") == "message"
            and SLACK_BOT_USER_ID
            and f"<@{SLACK_BOT_USER_ID}>" in message_text
        )
        if not (has_username_mention or has_id_mention):
            return {"status": "ignored", "reason": "Not an app_mention event"}

    if event.get("subtype") == "bot_message" or event.get("bot_id"):
        return {"status": "ignored", "reason": "Event from a bot"}

    channel_id = event.get("channel", "")
    event_ts = event.get("ts", "")
    thread_ts = event.get("thread_ts") or event_ts
    user_id = event.get("user", "")
    text = event.get("text", "")
    if not channel_id or not event_ts or not thread_ts:
        return {"status": "ignored", "reason": "Missing channel/thread timestamp"}

    bot_user_id = SLACK_BOT_USER_ID
    if not bot_user_id:
        authorizations = payload.get("authorizations", [])
        if isinstance(authorizations, list) and authorizations:
            auth_user_id = authorizations[0].get("user_id")
            if isinstance(auth_user_id, str):
                bot_user_id = auth_user_id
    if not bot_user_id:
        authed_users = payload.get("authed_users", [])
        if isinstance(authed_users, list) and authed_users:
            first_user = authed_users[0]
            if isinstance(first_user, str):
                bot_user_id = first_user

    if bot_user_id and user_id == bot_user_id:
        return {"status": "ignored", "reason": "Event from this bot user"}

    clean_text = strip_bot_mention(text, bot_user_id, bot_username=SLACK_BOT_USERNAME)
    pr_ref = parse_slack_review_command(clean_text)
    if pr_ref:
        if not await _is_repo_enabled_for_review({"owner": pr_ref.owner, "name": pr_ref.repo}):
            return {"status": "ignored", "reason": "Repository not enabled for review"}
        background_tasks.add_task(process_slack_pr_review_request, pr_ref, channel_id, thread_ts)
        return {"status": "accepted", "message": "Slack PR review request queued"}

    if looks_like_slack_pr_review_command(clean_text):
        background_tasks.add_task(
            post_slack_thread_reply,
            channel_id,
            thread_ts,
            "To request a PR review, use `@open-swe review https://github.com/OWNER/REPO/pull/NUMBER`.",
        )
        return {"status": "ignored", "reason": "Malformed Slack PR review command"}

    event_data = {
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "event_ts": event_ts,
        "user_id": user_id,
        "text": text,
        "bot_user_id": bot_user_id,
    }
    repo_config = await get_slack_repo_config(channel_id, thread_ts, slack_user_id=user_id)

    background_tasks.add_task(process_slack_mention, event_data, repo_config)

    return {"status": "accepted", "message": "Slack mention queued"}


@app.get("/webhooks/slack")
async def slack_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Slack webhook setup."""
    return {"status": "ok", "message": "Slack webhook endpoint is active"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


_SUPPORTED_GH_EVENTS = frozenset(
    [
        "issue_comment",
        "issues",
        "pull_request",
        "pull_request_review_comment",
        "pull_request_review",
        "push",
    ]
)
_SUPPORTED_GH_ISSUE_ACTIONS = frozenset(["edited", "opened", "reopened"])
_SUPPORTED_GH_PULL_REQUEST_ACTIONS = frozenset(
    [
        "review_requested",
        "opened",
        "ready_for_review",
        "converted_to_draft",
        "closed",
        "reopened",
    ]
)
_GH_PR_WATCH_TOGGLE_ACTIONS = frozenset(["closed", "reopened", "converted_to_draft"])
_GH_PR_FIRST_REVIEW_ACTIONS = frozenset(["opened", "ready_for_review"])
_SUPPORTED_GH_COMMENT_ACTIONS = {
    "issue_comment": frozenset(["created", "edited"]),
    "pull_request_review_comment": frozenset(["created", "edited"]),
    "pull_request_review": frozenset(["submitted", "edited"]),
}


def _build_github_issue_comments_text(comments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for comment in comments:
        body = comment.get("body", "")
        if not body or any(body.startswith(prefix) for prefix in _GITHUB_BOT_MESSAGE_PREFIXES):
            continue
        author = comment.get("author", "unknown")
        formatted_body = format_github_comment_body_for_prompt(author, body)
        lines.append(f"\n**{author}:**\n{formatted_body}\n")

    if not lines:
        return ""
    return "\n\n## Comments:\n" + "".join(lines)


def build_github_issue_prompt(
    repo_config: dict[str, str],
    issue_number: int,
    issue_id: str,
    title: str,
    body: str,
    comments: list[dict[str, Any]],
    *,
    github_login: str,
    issue_author: str = "",
) -> str:
    """Build the user prompt for a GitHub issue-triggered run."""
    triggered_by_line = f"## Triggered by: {github_login}\n\n" if github_login else ""
    comments_text = _build_github_issue_comments_text(comments)
    sanitized_title = sanitize_github_comment_body(title)
    formatted_body = format_github_comment_body_for_prompt(issue_author or github_login, body)
    return (
        "Please work on the following GitHub issue:\n\n"
        f"## Repository: {repo_config.get('owner')}/{repo_config.get('name')}\n\n"
        f"{triggered_by_line}"
        f"## GitHub Issue: #{issue_number} - Issue ID: {issue_id}\n\n"
        f"## Title: {sanitized_title}\n\n"
        f"## Description:\n{formatted_body}\n"
        f"{comments_text}\n\n"
        "Please analyze this issue and implement the necessary changes. "
        "When you need to communicate on GitHub, use `GH_TOKEN=dummy gh issue comment` "
        "with the issue number."
    )


def build_github_issue_followup_prompt(github_login: str, comment_body: str) -> str:
    """Build the prompt for a follow-up GitHub issue comment."""
    return (
        f"**{github_login}:**\n{format_github_comment_body_for_prompt(github_login, comment_body)}"
    )


def build_github_issue_update_prompt(github_login: str, title: str, body: str) -> str:
    """Build the prompt for a follow-up GitHub issue title/body update."""
    sanitized_title = sanitize_github_comment_body(title)
    formatted_body = format_github_comment_body_for_prompt(github_login, body)
    return (
        f"**{github_login}:** updated the GitHub issue title/body.\n\n"
        f"Title: {sanitized_title}\n\n"
        f"Description:\n{formatted_body}"
    )


async def _trigger_or_queue_run(
    thread_id: str,
    prompt: str,
    *,
    github_login: str,
    github_user_id: int | None,
    repo_config: dict[str, str],
    pr_number: int,
) -> None:
    """Create a new agent run or queue the message if the thread is busy."""
    thread_active = await is_thread_active(thread_id)
    if thread_active:
        logger.info("Thread %s is busy, queuing GitHub PR comment message", thread_id)
        await queue_message_for_thread(thread_id, prompt)
        return

    logger.info("Creating LangGraph run for thread %s from GitHub PR comment", thread_id)
    langgraph_client = get_client(url=LANGGRAPH_URL)
    await langgraph_client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": prompt}]},
        config={
            "configurable": {
                "source": "github",
                "github_login": github_login,
                "github_user_id": github_user_id,
                "repo": repo_config,
                "pr_number": pr_number,
            },
            "metadata": _AGENT_VERSION_METADATA,
        },
        if_not_exists="create",
    )
    logger.info("LangGraph run created for thread %s from GitHub PR comment", thread_id)


def _is_open_swe_reviewer_request(payload: dict[str, Any]) -> bool:
    reviewer = payload.get("requested_reviewer") or {}
    login = reviewer.get("login", "") if isinstance(reviewer, dict) else ""
    return login.lower() == OPEN_SWE_BOT_NAME.lower()


def build_github_pr_review_prompt(
    repo_config: dict[str, str],
    pr_number: int,
    pr_url: str,
    base_sha: str,
    head_sha: str,
) -> str:
    """Build the user prompt for a reviewer-agent run."""
    return (
        "Please review this GitHub pull request.\n\n"
        f"## Repository: {repo_config.get('owner')}/{repo_config.get('name')}\n\n"
        f"## Pull Request: {pr_url}\n\n"
        f"## PR Number: {pr_number}\n\n"
        f"## Base SHA: {base_sha}\n\n"
        f"## Head SHA: {head_sha}\n\n"
        "Submit findings as inline GitHub review comments. If there are no real issues, "
        "submit no comments."
    )


async def fetch_github_pr_metadata(pr_ref: GitHubPrRef, *, token: str) -> dict[str, Any] | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(
                f"https://api.github.com/repos/{pr_ref.owner}/{pr_ref.repo}/pulls/{pr_ref.number}",
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception(
                "Failed to fetch PR metadata for %s/%s#%s",
                pr_ref.owner,
                pr_ref.repo,
                pr_ref.number,
            )
            return None
    data = response.json()
    return data if isinstance(data, dict) else None


async def trigger_pr_review_from_ref(
    pr_ref: GitHubPrRef,
    *,
    source: str,
    github_login: str = "",
    github_user_id: int | None = None,
    slack_channel_id: str = "",
    slack_thread_ts: str = "",
) -> dict[str, Any]:
    repo_config = {"owner": pr_ref.owner, "name": pr_ref.repo}
    if not await _is_repo_enabled_for_review(repo_config):
        return {"success": False, "error": "Repository not enabled for review"}

    app_token, app_token_expires_at = await get_github_app_installation_token_with_expiry()
    if not app_token:
        logger.warning("No GitHub App token available for PR reviewer request")
        return {"success": False, "error": "No GitHub App token available"}

    pr_metadata = await fetch_github_pr_metadata(pr_ref, token=app_token)
    if not pr_metadata:
        return {"success": False, "error": "Could not fetch pull request metadata"}

    base_sha = pr_metadata.get("base", {}).get("sha", "")
    head = pr_metadata.get("head", {})
    head_sha = head.get("sha", "")
    branch_name = head.get("ref", "")
    base_ref = pr_metadata.get("base", {}).get("ref", "")
    pr_title = pr_metadata.get("title", "")
    pr_url = pr_metadata.get("html_url", "") or pr_ref.url
    if not base_sha or not head_sha:
        logger.warning("Missing base/head SHA for Slack PR review request")
        return {"success": False, "error": "Pull request metadata is missing base/head SHA"}

    thread_id = generate_reviewer_thread_id(pr_ref.owner, pr_ref.repo, pr_ref.number)
    langgraph_client = get_client(url=LANGGRAPH_URL)
    if not await _ensure_thread_exists_for_metadata(thread_id, langgraph_client):
        return {"success": False, "error": "Could not create reviewer thread"}

    try:
        await persist_encrypted_github_token(thread_id, app_token, expires_at=app_token_expires_at)
    except Exception:
        logger.warning("Could not persist bot token for reviewer thread %s", thread_id)
        return {"success": False, "error": "Could not persist reviewer token"}

    pr_meta: ReviewerPRMeta = {
        "owner": pr_ref.owner,
        "name": pr_ref.repo,
        "number": pr_ref.number,
        "url": pr_url,
        "title": pr_title,
        "head_ref": branch_name,
        "base_ref": base_ref,
    }
    slack_thread_meta: ReviewerSlackThread | None = None
    if slack_channel_id and slack_thread_ts:
        slack_thread_meta = {
            "channel_id": slack_channel_id,
            "thread_ts": slack_thread_ts,
        }
    await set_reviewer_thread_metadata(
        thread_id, pr=pr_meta, watch=True, slack_thread=slack_thread_meta
    )

    prompt = build_github_pr_review_prompt(repo_config, pr_ref.number, pr_url, base_sha, head_sha)
    configurable = _build_reviewer_configurable(
        source=source,
        github_login=github_login,
        github_user_id=github_user_id,
        repo_config=repo_config,
        pr_number=pr_ref.number,
        pr_url=pr_url,
        base_sha=base_sha,
        head_sha=head_sha,
        branch_name=branch_name,
        slack_channel_id=slack_channel_id,
        slack_thread_ts=slack_thread_ts,
    )

    thread_active = await is_thread_active(thread_id)
    if thread_active:
        logger.info("Reviewer thread %s is busy, queuing PR review request", thread_id)
        queued = await queue_message_for_thread(thread_id, prompt)
        return {"success": queued, "queued": queued, "thread_id": thread_id, "pr_url": pr_url}

    logger.info("Creating reviewer run for thread %s from %s PR review request", thread_id, source)
    await langgraph_client.runs.create(
        thread_id,
        "reviewer",
        input={"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": configurable, "metadata": _AGENT_VERSION_METADATA},
        if_not_exists="create",
    )
    return {"success": True, "queued": False, "thread_id": thread_id, "pr_url": pr_url}


def _build_reviewer_configurable(
    *,
    source: str,
    github_login: str,
    github_user_id: int | None,
    repo_config: dict[str, str],
    pr_number: int,
    pr_url: str,
    base_sha: str,
    head_sha: str,
    branch_name: str,
    re_review: bool = False,
    last_reviewed_sha: str = "",
    slack_channel_id: str = "",
    slack_thread_ts: str = "",
) -> dict[str, Any]:
    """Assemble the runnable-config ``configurable`` dict for a reviewer run."""
    configurable: dict[str, Any] = {
        "source": source,
        "github_login": github_login,
        "github_user_id": github_user_id,
        "repo": repo_config,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "review_requested": True,
        "re_review": re_review,
    }
    if branch_name:
        configurable["branch_name"] = branch_name
    if last_reviewed_sha:
        configurable["last_reviewed_sha"] = last_reviewed_sha
    if slack_channel_id and slack_thread_ts:
        configurable["slack_thread"] = {
            "channel_id": slack_channel_id,
            "thread_ts": slack_thread_ts,
        }
    return configurable


async def _draft_review_enabled_for_author(author_login: str) -> bool:
    """Return whether draft PRs by ``author_login`` should auto-review.

    Tri-state: the PR author's profile ``review_draft_prs`` wins when set to
    True/False; ``None`` (or no profile, e.g. external contributors) falls
    back to the team-wide default.
    """
    if author_login:
        profile = await get_profile(author_login)
        if isinstance(profile, dict):
            override = profile.get("review_draft_prs")
            if isinstance(override, bool):
                return override
    team = await get_team_settings()
    return bool(team.get("review_draft_prs"))


async def _dispatch_first_review_from_pr_payload(payload: dict[str, Any], *, source: str) -> None:
    """Trigger a first-review run on the canonical reviewer thread for a PR."""
    repo = payload.get("repository", {})
    pull_request = payload.get("pull_request", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    pr_number = pull_request.get("number")
    pr_url = pull_request.get("html_url", "") or pull_request.get("url", "")
    branch_name = pull_request.get("head", {}).get("ref", "")
    base_ref = pull_request.get("base", {}).get("ref", "")
    base_sha = pull_request.get("base", {}).get("sha", "")
    head_sha = pull_request.get("head", {}).get("sha", "")
    pr_title = pull_request.get("title", "")
    github_login = payload.get("sender", {}).get("login", "")
    github_user_id = payload.get("sender", {}).get("id")

    if not pr_number or not pr_url or not base_sha or not head_sha:
        logger.warning("Missing PR context for reviewer dispatch, skipping run")
        return

    thread_id = generate_reviewer_thread_id(
        repo_config.get("owner", ""), repo_config.get("name", ""), pr_number
    )

    app_token, app_token_expires_at = await get_github_app_installation_token_with_expiry()
    if not app_token:
        logger.warning("No GitHub App token available for reviewer dispatch")
        return

    langgraph_client = get_client(url=LANGGRAPH_URL)
    if not await _ensure_thread_exists_for_metadata(thread_id, langgraph_client):
        return

    try:
        await persist_encrypted_github_token(thread_id, app_token, expires_at=app_token_expires_at)
    except Exception:
        logger.warning("Could not persist bot token for reviewer thread %s", thread_id)
        return

    pr_meta: ReviewerPRMeta = {
        "owner": repo_config.get("owner", ""),
        "name": repo_config.get("name", ""),
        "number": pr_number,
        "url": pr_url,
        "title": pr_title,
        "head_ref": branch_name,
        "base_ref": base_ref,
    }
    await set_reviewer_thread_metadata(thread_id, pr=pr_meta, watch=True)

    prompt = build_github_pr_review_prompt(repo_config, pr_number, pr_url, base_sha, head_sha)
    configurable = _build_reviewer_configurable(
        source=source,
        github_login=github_login,
        github_user_id=github_user_id,
        repo_config=repo_config,
        pr_number=pr_number,
        pr_url=pr_url,
        base_sha=base_sha,
        head_sha=head_sha,
        branch_name=branch_name,
    )

    thread_active = await is_thread_active(thread_id)
    if thread_active:
        logger.info("Reviewer thread %s is busy, queuing PR review (source=%s)", thread_id, source)
        await queue_message_for_thread(thread_id, prompt)
        return

    logger.info("Creating reviewer run for thread %s (source=%s)", thread_id, source)
    await langgraph_client.runs.create(
        thread_id,
        "reviewer",
        input={"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": configurable, "metadata": _AGENT_VERSION_METADATA},
        if_not_exists="create",
    )
    logger.info("Reviewer run created for thread %s (source=%s)", thread_id, source)


async def process_github_pr_review_request(payload: dict[str, Any]) -> None:
    """Trigger the reviewer agent when the Open SWE bot is requested on a PR."""
    await _dispatch_first_review_from_pr_payload(payload, source="github")


async def process_github_pr_ready(payload: dict[str, Any]) -> None:
    """Auto-review a PR that has just been opened or marked ready-for-review.

    Drafts are gated by the PR author's ``review_draft_prs`` profile flag
    (with the team-wide setting as a fallback).
    """
    pull_request = payload.get("pull_request", {})
    is_draft = bool(pull_request.get("draft"))
    if is_draft:
        author = pull_request.get("user") or {}
        author_login = author.get("login", "") if isinstance(author, dict) else ""
        if not await _draft_review_enabled_for_author(author_login):
            logger.info(
                "Skipping auto-review of draft PR by %s: review_draft_prs is disabled",
                author_login or "<unknown>",
            )
            return
    await _dispatch_first_review_from_pr_payload(payload, source="github_auto")


async def process_github_pr_review_command(
    payload: dict[str, Any],
    event_type: str,
    pr_url_override: str | None,
) -> None:
    """Trigger the reviewer when a PR comment contains ``@open-swe review``.

    ``pr_url_override`` is the optional URL token that followed ``review``. If
    set, the review targets that PR; otherwise the comment's own PR is used.
    """
    repo = payload.get("repository", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    pr_data = payload.get("pull_request") or payload.get("issue", {})
    sender = payload.get("sender", {})
    github_login = sender.get("login", "")
    github_user_id = sender.get("id")

    pr_ref: GitHubPrRef | None = None
    if pr_url_override:
        pr_ref = parse_github_pr_url(pr_url_override)
        if pr_ref is None:
            logger.info("Ignoring @open-swe review with unparseable URL %s", pr_url_override)
            return
    else:
        pr_number = pr_data.get("number")
        if not pr_number:
            logger.warning("@open-swe review command missing pr_number, skipping")
            return
        pr_ref = GitHubPrRef(
            owner=repo_config["owner"],
            repo=repo_config["name"],
            number=pr_number,
            url=pr_data.get("html_url", "") or pr_data.get("url", ""),
        )

    comment = payload.get("comment") or payload.get("review", {})
    comment_id = comment.get("id")
    node_id = comment.get("node_id") if event_type == "pull_request_review" else None
    if comment_id:
        app_token = await get_github_app_installation_token()
        if app_token:
            await react_to_github_comment(
                repo_config,
                comment_id,
                event_type=event_type,
                token=app_token,
                pull_number=pr_data.get("number"),
                node_id=node_id,
            )

    result = await trigger_pr_review_from_ref(
        pr_ref,
        source="github",
        github_login=github_login,
        github_user_id=github_user_id,
    )
    if not result.get("success"):
        logger.warning(
            "Failed to trigger reviewer from @open-swe review on %s/%s#%s: %s",
            pr_ref.owner,
            pr_ref.repo,
            pr_ref.number,
            result.get("error"),
        )


async def _fetch_open_pr_for_branch(
    repo_config: dict[str, str], head_ref: str, *, token: str
) -> dict[str, Any] | None:
    """Find the open PR whose head ref matches ``head_ref``, if one exists."""
    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"state": "open", "head": f"{owner}:{head_ref}", "per_page": 1}
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Failed to look up open PR for %s/%s head=%s", owner, repo, head_ref)
            return None
    data = response.json()
    if not isinstance(data, list) or not data:
        return None
    pr = data[0]
    return pr if isinstance(pr, dict) else None


async def _get_thread_metadata_safe(thread_id: str) -> dict[str, Any] | None:
    """Fetch a thread's metadata; return ``None`` if the thread doesn't exist."""
    langgraph_client = get_client(url=LANGGRAPH_URL)
    try:
        thread = await langgraph_client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        if _is_not_found_error(exc):
            return None
        logger.warning("Failed to fetch reviewer thread metadata for %s", thread_id)
        return None
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    return metadata if isinstance(metadata, dict) else {}


async def process_github_pr_close(payload: dict[str, Any]) -> None:
    """Toggle watch on the canonical reviewer thread on close/reopen/draft transitions.

    ``reopened`` re-enables watch; ``closed`` and ``converted_to_draft`` disable it.
    """
    repo = payload.get("repository", {})
    pull_request = payload.get("pull_request", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    pr_number = pull_request.get("number")
    if not pr_number or not isinstance(pr_number, int):
        return
    if not await _is_repo_enabled_for_review(repo_config):
        return

    thread_id = generate_reviewer_thread_id(
        repo_config.get("owner", ""), repo_config.get("name", ""), pr_number
    )
    metadata = await _get_thread_metadata_safe(thread_id)
    if metadata is None or metadata.get("kind") != REVIEWER_THREAD_KIND:
        # No reviewer thread for this PR, nothing to do.
        logger.debug(
            "PR %s/%s#%s closed/reopened: no reviewer thread, skipping watch update",
            repo_config.get("owner"),
            repo_config.get("name"),
            pr_number,
        )
        return
    action = payload.get("action", "")
    desired_watch = action == "reopened"
    if metadata.get("watch") == desired_watch:
        return
    await set_reviewer_thread_metadata(thread_id, watch=desired_watch)
    logger.info("Set watch=%s on reviewer thread %s after PR %s", desired_watch, thread_id, action)


async def process_github_push_event(payload: dict[str, Any]) -> None:
    """Re-trigger the reviewer for a watched PR when its head branch is pushed to."""
    ref = payload.get("ref", "")
    after_sha = payload.get("after", "")
    if not ref.startswith("refs/heads/"):
        logger.debug("Push ignored: ref %s is not a branch", ref)
        return
    if not isinstance(after_sha, str) or not after_sha or set(after_sha) == {"0"}:
        logger.debug("Push to %s ignored: branch deletion or missing SHA", ref)
        return
    head_ref = ref[len("refs/heads/") :]

    repo = payload.get("repository", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", "") or repo.get("owner", {}).get("name", ""),
        "name": repo.get("name", ""),
    }
    if not repo_config["owner"] or not repo_config["name"]:
        logger.warning("Push to %s ignored: repository owner/name missing from payload", head_ref)
        return
    if not await _is_repo_enabled_for_review(repo_config):
        logger.info(
            "Push to %s/%s head=%s ignored: repo not enabled for review",
            repo_config["owner"],
            repo_config["name"],
            head_ref,
        )
        return

    app_token, app_token_expires_at = await get_github_app_installation_token_with_expiry()
    if not app_token:
        logger.warning("No GitHub App token for push re-review on %s", head_ref)
        return

    pr = await _fetch_open_pr_for_branch(repo_config, head_ref, token=app_token)
    if not pr:
        logger.debug(
            "No open PR found for push to %s/%s head=%s",
            repo_config["owner"],
            repo_config["name"],
            head_ref,
        )
        return

    pr_number = pr.get("number")
    pr_url = pr.get("html_url") or pr.get("url") or ""
    base_sha = pr.get("base", {}).get("sha", "")
    base_ref = pr.get("base", {}).get("ref", "")
    head_sha = pr.get("head", {}).get("sha", after_sha)
    pr_title = pr.get("title", "")
    if not isinstance(pr_number, int) or not base_sha or not head_sha:
        logger.warning(
            "Push to %s/%s head=%s ignored: PR metadata missing number/base/head SHA",
            repo_config["owner"],
            repo_config["name"],
            head_ref,
        )
        return

    thread_id = generate_reviewer_thread_id(repo_config["owner"], repo_config["name"], pr_number)
    metadata = await _get_thread_metadata_safe(thread_id)
    if metadata is None or metadata.get("kind") != REVIEWER_THREAD_KIND:
        logger.info(
            "Push to %s/%s#%s ignored: no reviewer thread for this PR. "
            "Trigger a first review (Slack `@open-swe review <url>` or request "
            "open-swe[bot] as a GitHub reviewer) to start watching.",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
        )
        return
    if not metadata.get("watch"):
        logger.info("Push to %s ignored: reviewer thread %s is not watching", head_ref, thread_id)
        return

    last_reviewed_sha = metadata.get("last_reviewed_sha")
    if isinstance(last_reviewed_sha, str) and last_reviewed_sha == head_sha:
        logger.info("Push to %s ignored: head_sha unchanged from last_reviewed_sha", head_ref)
        return

    langgraph_client = get_client(url=LANGGRAPH_URL)
    if not await _ensure_thread_exists_for_metadata(thread_id, langgraph_client):
        return
    try:
        await persist_encrypted_github_token(thread_id, app_token, expires_at=app_token_expires_at)
    except Exception:
        logger.warning("Could not persist bot token for reviewer thread %s", thread_id)
        return

    pr_meta: ReviewerPRMeta = {
        "owner": repo_config["owner"],
        "name": repo_config["name"],
        "number": pr_number,
        "url": pr_url,
        "title": pr_title,
        "head_ref": head_ref,
        "base_ref": base_ref,
    }
    await set_reviewer_thread_metadata(thread_id, pr=pr_meta, watch=True)

    re_review_prompt = (
        f"A new commit has been pushed to PR #{pr_number}. The new HEAD is "
        f"{head_sha}. Reconcile existing findings against the new diff, add any "
        f"net-new findings, and call `publish_review` once you're done."
    )
    configurable = _build_reviewer_configurable(
        source="github_push",
        github_login=payload.get("sender", {}).get("login", "") or "",
        github_user_id=payload.get("sender", {}).get("id"),
        repo_config=repo_config,
        pr_number=pr_number,
        pr_url=pr_url,
        base_sha=base_sha,
        head_sha=head_sha,
        branch_name=head_ref,
        re_review=True,
        last_reviewed_sha=last_reviewed_sha if isinstance(last_reviewed_sha, str) else "",
    )

    thread_active = await is_thread_active(thread_id)
    if thread_active:
        logger.info("Reviewer thread %s busy, queuing push re-review", thread_id)
        await queue_message_for_thread(thread_id, re_review_prompt)
        return

    logger.info("Creating push re-review run for thread %s", thread_id)
    await langgraph_client.runs.create(
        thread_id,
        "reviewer",
        input={"messages": [{"role": "user", "content": re_review_prompt}]},
        config={"configurable": configurable, "metadata": _AGENT_VERSION_METADATA},
        if_not_exists="create",
    )


async def _refresh_thread_github_token_after_401(thread_id: str, email: str) -> str | None:
    """Invalidate the cached token after a 401 and try to resolve a fresh one."""
    logger.warning(
        "GitHub returned 401 for thread %s; invalidating cached token and re-resolving",
        thread_id,
    )
    await invalidate_cached_github_token(thread_id)
    return await _get_or_resolve_thread_github_token(thread_id, email)


async def _get_or_resolve_thread_github_token(thread_id: str, email: str) -> str | None:
    """Resolve and persist a GitHub token for a thread when available.

    Skips the cached ciphertext when its ``github_token_expires_at`` is past.
    In bot-token-only mode, returns a fresh GitHub App installation token
    instead of resolving per-user OAuth tokens.
    """
    if is_bot_token_only_mode():
        bot_token, expires_at = await get_github_app_installation_token_with_expiry()
        if bot_token:
            try:
                await persist_encrypted_github_token(thread_id, bot_token, expires_at=expires_at)
            except Exception:
                logger.warning("Could not persist bot token for thread %s", thread_id)
            return bot_token
        logger.warning("Bot-token-only mode but GitHub App token unavailable")
        return None

    github_token, _encrypted_token, _expires_at = await get_github_token_from_thread(thread_id)
    if github_token:
        return github_token

    auth_result = await resolve_github_token_from_email(email)
    github_token = auth_result.get("token")
    if not github_token:
        return None

    try:
        await persist_encrypted_github_token(
            thread_id, github_token, expires_at=auth_result.get("expires_at")
        )
    except Exception:
        logger.warning("Could not persist GitHub token for thread %s", thread_id)
    return github_token


async def process_github_pr_comment(payload: dict[str, Any], event_type: str) -> None:
    """Process a GitHub PR comment that tagged @open-swe.

    Retrieves the existing thread token, reacts with 👀, fetches all comments
    since the last @open-swe tag, then creates or queues a new run.

    Args:
        payload: The parsed GitHub webhook payload.
        event_type: One of 'issue_comment', 'pull_request_review_comment',
                    'pull_request_review'.
    """
    (
        repo_config,
        pr_number,
        branch_name,
        github_login,
        pr_url,
        comment_id,
        node_id,
    ) = await extract_pr_context(payload, event_type)
    github_user_id = payload.get("sender", {}).get("id")

    logger.info(
        "Processing GitHub PR comment: event=%s, pr=%s, branch=%s",
        event_type,
        pr_number,
        branch_name,
    )

    thread_id = get_thread_id_from_branch(branch_name) if branch_name else None
    if not thread_id:
        if not pr_number:
            logger.warning(
                "Could not determine thread_id for branch '%s' (no pr_number), skipping",
                branch_name,
            )
            return
        owner = repo_config.get("owner", "")
        name = repo_config.get("name", "")
        stable_key = f"{owner}/{name}/pr/{pr_number}"
        thread_id = str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key))
        logger.info("Generated thread_id %s for non-open-swe branch '%s'", thread_id, branch_name)
        langgraph_client = get_client(url=LANGGRAPH_URL)
        try:
            await langgraph_client.threads.update(thread_id, metadata={"branch_name": branch_name})
        except Exception as exc:  # noqa: BLE001
            if _is_not_found_error(exc):
                await langgraph_client.threads.create(
                    thread_id=thread_id,
                    if_exists="do_nothing",
                    metadata={"branch_name": branch_name},
                )
            else:
                logger.warning("Failed to persist branch_name metadata for thread %s", thread_id)

    email = GITHUB_USER_EMAIL_MAP.get(github_login, "")
    if not email:
        logger.warning("No email mapping for GitHub user '%s', skipping", github_login)
        return

    github_token = await _get_or_resolve_thread_github_token(thread_id, email)
    if not github_token:
        logger.warning("No GitHub token for thread %s, skipping", thread_id)
        return

    if comment_id:
        try:
            await react_to_github_comment(
                repo_config,
                comment_id,
                event_type=event_type,
                token=github_token,
                pull_number=pr_number,
                node_id=node_id,
            )
        except GitHubAuthError:
            github_token = await _refresh_thread_github_token_after_401(thread_id, email)
            if not github_token:
                logger.warning("Re-auth failed for thread %s after 401; skipping", thread_id)
                return
            await react_to_github_comment(
                repo_config,
                comment_id,
                event_type=event_type,
                token=github_token,
                pull_number=pr_number,
                node_id=node_id,
            )

    if not pr_number:
        logger.warning("No PR number found in payload, skipping")
        return

    try:
        comments = await fetch_pr_comments_since_last_tag(
            repo_config, pr_number, token=github_token
        )
    except GitHubAuthError:
        github_token = await _refresh_thread_github_token_after_401(thread_id, email)
        if not github_token:
            logger.warning("Re-auth failed for thread %s after 401; skipping", thread_id)
            return
        comments = await fetch_pr_comments_since_last_tag(
            repo_config, pr_number, token=github_token
        )
    if not comments:
        logger.info("No comments found since last @open-swe tag for PR %s", pr_number)
        return

    prompt = build_pr_prompt(comments, pr_url, repo_config=repo_config)
    await _trigger_or_queue_run(
        thread_id,
        prompt,
        github_login=github_login,
        github_user_id=github_user_id,
        repo_config=repo_config,
        pr_number=pr_number,
    )


async def process_github_issue(payload: dict[str, Any], event_type: str) -> None:
    """Process a GitHub issue or issue comment that tagged @open-swe."""
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }

    issue_id = str(issue.get("id", ""))
    issue_number = issue.get("number")
    github_login = payload.get("sender", {}).get("login", "")
    github_user_id = payload.get("sender", {}).get("id")
    issue_url = issue.get("html_url", "") or issue.get("url", "")
    title = issue.get("title", "No title")
    description = issue.get("body") or "No description"
    issue_author = issue.get("user", {}).get("login", "")

    logger.info(
        "Processing GitHub issue: event=%s, issue=%s, repo=%s/%s",
        event_type,
        issue_number,
        repo_config.get("owner"),
        repo_config.get("name"),
    )

    if not issue_id or not issue_number:
        logger.warning("Missing GitHub issue id/number, skipping")
        return

    email = GITHUB_USER_EMAIL_MAP.get(github_login, "")
    if not email:
        logger.warning("No email mapping for GitHub user '%s', skipping", github_login)
        return

    thread_id = generate_thread_id_from_github_issue(issue_id)
    existing_thread = await _thread_exists(thread_id)
    github_token = await _get_or_resolve_thread_github_token(thread_id, email)
    app_token = await get_github_app_installation_token()
    reaction_token = github_token or app_token
    comment = payload.get("comment", {})
    comment_id = comment.get("id")
    if event_type == "issue_comment" and comment_id:
        if not reaction_token:
            logger.warning("No GitHub token available to react to issue comment %s", comment_id)
        else:
            try:
                reacted = await react_to_github_comment(
                    repo_config,
                    comment_id,
                    event_type="issue_comment",
                    token=reaction_token,
                )
            except GitHubAuthError:
                github_token = await _refresh_thread_github_token_after_401(thread_id, email)
                reaction_token = github_token or app_token
                reacted = False
                if reaction_token:
                    try:
                        reacted = await react_to_github_comment(
                            repo_config,
                            comment_id,
                            event_type="issue_comment",
                            token=reaction_token,
                        )
                    except GitHubAuthError:
                        logger.warning(
                            "Re-auth still produced 401 reacting to issue comment %s",
                            comment_id,
                        )
                        reacted = False
            if not reacted:
                logger.warning("Failed to react to GitHub issue comment %s", comment_id)

    if existing_thread:
        if event_type == "issue_comment":
            prompt = build_github_issue_followup_prompt(
                comment.get("user", {}).get("login", github_login) or github_login,
                comment.get("body", ""),
            )
        else:
            prompt = build_github_issue_update_prompt(github_login, title, description)
    else:
        try:
            comments = await fetch_issue_comments(
                repo_config, issue_number, token=github_token or app_token
            )
        except GitHubAuthError:
            github_token = await _refresh_thread_github_token_after_401(thread_id, email)
            comments = await fetch_issue_comments(
                repo_config, issue_number, token=github_token or app_token
            )
        if comment_id and not any(item.get("comment_id") == comment_id for item in comments):
            comments.append(
                {
                    "body": comment.get("body", ""),
                    "author": comment.get("user", {}).get("login", "unknown"),
                    "created_at": comment.get("created_at", ""),
                    "comment_id": comment_id,
                }
            )
            comments.sort(key=lambda item: item.get("created_at", ""))

        prompt = build_github_issue_prompt(
            repo_config,
            issue_number,
            issue_id,
            title,
            description,
            comments,
            github_login=github_login,
            issue_author=issue_author,
        )
    configurable: dict[str, Any] = {
        "source": "github",
        "github_login": github_login,
        "github_user_id": github_user_id,
        "repo": repo_config,
        "github_issue": {
            "id": issue_id,
            "number": issue_number,
            "title": title,
            "url": issue_url,
        },
    }

    thread_active = await is_thread_active(thread_id)
    if thread_active:
        logger.info("Thread %s is busy, queuing GitHub issue message", thread_id)
        await queue_message_for_thread(thread_id, prompt)
        return

    logger.info("Creating LangGraph run for thread %s from GitHub issue", thread_id)
    langgraph_client = get_client(url=LANGGRAPH_URL)
    await langgraph_client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": configurable, "metadata": _AGENT_VERSION_METADATA},
        if_not_exists="create",
    )
    logger.info("LangGraph run created for thread %s from GitHub issue", thread_id)


@app.post("/webhooks/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Handle GitHub webhooks for issue and PR events that tag @open-swe."""
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_github_signature(body, signature, secret=GITHUB_WEBHOOK_SECRET):
        logger.warning("Invalid GitHub webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type not in _SUPPORTED_GH_EVENTS:
        logger.info("Ignoring unsupported GitHub event type: %s", event_type)
        return {"status": "ignored", "reason": f"Unsupported event type: {event_type}"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse GitHub webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    webhook_repo = payload.get("repository", {})
    webhook_repo_config = {
        "owner": webhook_repo.get("owner", {}).get("login", ""),
        "name": webhook_repo.get("name", ""),
    }

    issue = payload.get("issue", {})
    is_pull_request_comment = bool(event_type == "issue_comment" and issue.get("pull_request"))
    is_issue_comment = bool(event_type == "issue_comment" and not issue.get("pull_request"))
    is_issue_event = event_type == "issues"
    is_pull_request_event = event_type == "pull_request"

    if is_pull_request_event:
        action = payload.get("action", "")
        if action not in _SUPPORTED_GH_PULL_REQUEST_ACTIONS:
            logger.info("Ignoring unsupported GitHub pull_request action: %s", action)
            return {
                "status": "ignored",
                "reason": f"Unsupported GitHub pull_request action: {action}",
            }
        if action in _GH_PR_WATCH_TOGGLE_ACTIONS:
            if not await _is_repo_enabled_for_review(webhook_repo_config):
                return {"status": "ignored", "reason": "Repository not enabled for review"}
            logger.info("Accepted GitHub PR %s webhook, scheduling reviewer watch update", action)
            background_tasks.add_task(process_github_pr_close, payload)
            return {"status": "accepted", "message": f"Processing PR {action} for reviewer watch"}
        if action in _GH_PR_FIRST_REVIEW_ACTIONS:
            if not await _is_repo_enabled_for_review(webhook_repo_config):
                return {"status": "ignored", "reason": "Repository not enabled for review"}
            gate_rejection = await _enforce_public_repo_org_gate(payload, "pull_request")
            if gate_rejection is not None:
                return gate_rejection
            logger.info("Accepted GitHub PR %s webhook, scheduling auto-review task", action)
            background_tasks.add_task(process_github_pr_ready, payload)
            return {"status": "accepted", "message": f"Processing PR {action} for auto-review"}
        if not _is_open_swe_reviewer_request(payload):
            logger.info("Ignoring PR review request for a different reviewer")
            return {"status": "ignored", "reason": "Review request is not for open-swe bot"}
        if not await _is_repo_enabled_for_review(webhook_repo_config):
            logger.warning(
                "Rejecting GitHub reviewer webhook: repo '%s/%s' not enabled for review",
                webhook_repo_config.get("owner"),
                webhook_repo_config.get("name"),
            )
            return {"status": "ignored", "reason": "Repository not enabled for review"}

        gate_rejection = await _enforce_public_repo_org_gate(payload, "pull_request")
        if gate_rejection is not None:
            return gate_rejection

        logger.info("Accepted GitHub PR review request webhook, scheduling reviewer task")
        background_tasks.add_task(process_github_pr_review_request, payload)
        return {"status": "accepted", "message": "Processing GitHub PR review request"}

    if event_type == "push":
        if not await _is_repo_enabled_for_review(webhook_repo_config):
            return {"status": "ignored", "reason": "Repository not enabled for review"}
        logger.info("Accepted GitHub push webhook, scheduling reviewer watch evaluation")
        background_tasks.add_task(process_github_push_event, payload)
        return {"status": "accepted", "message": "Processing GitHub push for reviewer watch"}

    if not _is_repo_allowed(webhook_repo_config):
        logger.warning(
            "Rejecting GitHub webhook: repo '%s/%s' not in allowlist",
            webhook_repo_config.get("owner"),
            webhook_repo_config.get("name"),
        )
        return {"status": "ignored", "reason": "Repository not in allowlist"}

    if is_issue_event:
        action = payload.get("action", "")
        if action not in _SUPPORTED_GH_ISSUE_ACTIONS:
            logger.info("Ignoring unsupported GitHub issue action: %s", action)
            return {"status": "ignored", "reason": f"Unsupported GitHub issue action: {action}"}
        if action == "edited":
            changes = payload.get("changes", {})
            if not any(field in changes for field in ("body", "title")):
                logger.info("Ignoring GitHub issue edit without title/body changes")
                return {"status": "ignored", "reason": "Issue edit did not change title or body"}

        issue_text = f"{issue.get('title', '')}\n\n{issue.get('body', '')}".lower()
        if not any(tag in issue_text for tag in OPEN_SWE_TAGS):
            logger.info("Ignoring issue that does not mention @openswe or @open-swe")
            return {"status": "ignored", "reason": "Issue does not mention @openswe or @open-swe"}

        gate_rejection = await _enforce_public_repo_org_gate(payload, event_type)
        if gate_rejection is not None:
            return gate_rejection

        logger.info("Accepted GitHub issue webhook, scheduling background task")
        background_tasks.add_task(process_github_issue, payload, event_type)
        return {"status": "accepted", "message": "Processing GitHub issue event"}

    action = payload.get("action", "")
    supported_comment_actions = _SUPPORTED_GH_COMMENT_ACTIONS.get(event_type)
    if supported_comment_actions is None:
        logger.info("Ignoring unsupported GitHub payload shape for event=%s", event_type)
        return {"status": "ignored", "reason": f"Unsupported payload for event type: {event_type}"}
    if action and action not in supported_comment_actions:
        logger.debug("Ignoring unsupported GitHub %s action: %s", event_type, action)
        return {"status": "ignored", "reason": f"Unsupported GitHub {event_type} action: {action}"}

    comment = payload.get("comment") or payload.get("review", {})
    comment_body = (comment.get("body") or "") if comment else ""
    if not any(tag in comment_body.lower() for tag in OPEN_SWE_TAGS):
        logger.debug(
            "Ignoring GitHub %s%s that does not mention @openswe or @open-swe",
            event_type,
            f" action={action}" if action else "",
        )
        return {"status": "ignored", "reason": "Comment does not mention @openswe or @open-swe"}

    gate_rejection = await _enforce_public_repo_org_gate(payload, event_type)
    if gate_rejection is not None:
        return gate_rejection

    logger.info("Accepted GitHub webhook: event=%s, scheduling background task", event_type)
    if is_pull_request_comment or event_type in {
        "pull_request_review_comment",
        "pull_request_review",
    }:
        is_review_command, pr_url_override = parse_github_review_command(comment_body)
        if is_review_command:
            if not await _is_repo_enabled_for_review(webhook_repo_config):
                return {"status": "ignored", "reason": "Repository not enabled for review"}
            background_tasks.add_task(
                process_github_pr_review_command, payload, event_type, pr_url_override
            )
            return {"status": "accepted", "message": "Processing GitHub PR review command"}
        background_tasks.add_task(process_github_pr_comment, payload, event_type)
        return {"status": "accepted", "message": f"Processing {event_type} event"}

    if is_issue_comment:
        background_tasks.add_task(process_github_issue, payload, event_type)
        return {"status": "accepted", "message": "Processing GitHub issue comment event"}

    logger.info("Ignoring unsupported GitHub payload shape for event=%s", event_type)
    return {"status": "ignored", "reason": f"Unsupported payload for event type: {event_type}"}
