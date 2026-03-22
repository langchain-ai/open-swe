"""GitHub OAuth and LangSmith authentication utilities."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import jwt
from langgraph.config import get_config
from langgraph.graph.state import RunnableConfig
from langgraph_sdk import get_client

from ..encryption import encrypt_token
from .github_app import get_github_app_installation_token
from .github_token import get_github_token_from_thread
from .github_user_email_map import GITHUB_USER_EMAIL_MAP
from .linear import comment_on_linear_issue
from .slack import post_slack_ephemeral_message, post_slack_thread_reply

logger = logging.getLogger(__name__)

client = get_client()

LANGSMITH_API_KEY = os.environ.get("LANGSMITH_API_KEY_PROD", "")
LANGSMITH_API_URL = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
LANGSMITH_HOST_API_URL = os.environ.get("LANGSMITH_HOST_API_URL", "https://api.host.langchain.com")
GITHUB_OAUTH_PROVIDER_ID = os.environ.get("GITHUB_OAUTH_PROVIDER_ID", "")
X_SERVICE_AUTH_JWT_SECRET = os.environ.get("X_SERVICE_AUTH_JWT_SECRET", "")
USER_ID_API_KEY_MAP = os.environ.get("USER_ID_API_KEY_MAP", "")

logger.debug(
    "Auth env snapshot: LANGSMITH_API_KEY_PROD=%s LANGSMITH_ENDPOINT=%s "
    "LANGSMITH_HOST_API_URL=%s GITHUB_OAUTH_PROVIDER_ID=%s",
    "set" if LANGSMITH_API_KEY else "missing",
    "set" if LANGSMITH_API_URL else "missing",
    "set" if LANGSMITH_HOST_API_URL else "missing",
    "set" if GITHUB_OAUTH_PROVIDER_ID else "missing",
)


def is_bot_token_only_mode() -> bool:
    """Check if we're in bot-token-only mode.

    This is the case when LANGSMITH_API_KEY_PROD is set (deployed) but neither
    X_SERVICE_AUTH_JWT_SECRET nor USER_ID_API_KEY_MAP is configured, meaning we
    can't resolve per-user GitHub OAuth tokens. In this mode the GitHub App
    installation token is used for all git operations instead.
    """
    return bool(LANGSMITH_API_KEY and not X_SERVICE_AUTH_JWT_SECRET and not USER_ID_API_KEY_MAP)


def _retry_instruction(source: str) -> str:
    if source == "slack":
        return "Once authenticated, mention me again in this Slack thread to retry."
    return "Once authenticated, reply to this issue mentioning @openswe to retry."


def _source_account_label(source: str) -> str:
    if source == "slack":
        return "Slack"
    return "Linear"


def _auth_link_text(source: str, auth_url: str) -> str:
    if source == "slack":
        return auth_url
    return f"[Authenticate with GitHub]({auth_url})"


def _work_item_label(source: str) -> str:
    if source == "slack":
        return "thread"
    return "issue"


def get_secret_key_for_user(
    user_id: str, tenant_id: str, expiration_seconds: int = 300
) -> tuple[str, Literal["service", "api_key"]]:
    """Create a short-lived service JWT for authenticating as a specific user."""
    if not X_SERVICE_AUTH_JWT_SECRET:
        msg = "X_SERVICE_AUTH_JWT_SECRET is not configured. Cannot generate service keys."
        raise ValueError(msg)

    payload = {
        "sub": "unspecified",
        "exp": datetime.now(UTC) + timedelta(seconds=expiration_seconds),
        "user_id": user_id,
        "tenant_id": tenant_id,
    }
    return jwt.encode(payload, X_SERVICE_AUTH_JWT_SECRET, algorithm="HS256"), "service"


async def get_ls_user_id_from_email(email: str) -> dict[str, str | None]:
    """Get the LangSmith user ID and tenant ID from a user's email."""
    if not LANGSMITH_API_KEY:
        logger.warning("LangSmith API key not configured; cannot resolve LS user for %s", email)
        return {"ls_user_id": None, "tenant_id": None}

    url = f"{LANGSMITH_API_URL}/api/v1/workspaces/current/members/active"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                url,
                headers={"X-API-Key": LANGSMITH_API_KEY},
                params={"emails": [email]},
            )
            response.raise_for_status()
            members = response.json()

            if members and len(members) > 0:
                member = members[0]
                return {
                    "ls_user_id": member.get("ls_user_id"),
                    "tenant_id": member.get("tenant_id"),
                }
        except httpx.HTTPError:
            logger.debug("HTTP error getting LangSmith user info for email")
        return {"ls_user_id": None, "tenant_id": None}


async def get_github_token_for_user(ls_user_id: str, tenant_id: str) -> dict[str, Any]:
    """Get GitHub OAuth token for a user via LangSmith agent auth."""
    if not GITHUB_OAUTH_PROVIDER_ID:
        logger.error("GitHub auth failed: GITHUB_OAUTH_PROVIDER_ID is not configured")
        return {"error": "GITHUB_OAUTH_PROVIDER_ID not configured"}

    try:
        headers = {
            "X-Tenant-Id": tenant_id,
            "X-User-Id": ls_user_id,
        }
        secret_key, secret_type = get_secret_key_for_user(ls_user_id, tenant_id)
        if secret_type == "api_key":
            headers["X-API-Key"] = secret_key
        else:
            headers["X-Service-Key"] = secret_key

        payload = {
            "provider": GITHUB_OAUTH_PROVIDER_ID,
            "scopes": ["repo"],
            "user_id": ls_user_id,
            "ls_user_id": ls_user_id,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{LANGSMITH_HOST_API_URL}/v2/auth/authenticate",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            response_data = response.json()

            token = response_data.get("token")
            auth_url = response_data.get("url")

            if token:
                return {"token": token}
            if auth_url:
                return {"auth_url": auth_url}
            return {"error": "Unexpected auth response"}

    except httpx.HTTPStatusError as e:
        logger.error("GitHub auth API HTTP error: %s - %s", e.response.status_code, e.response.text)
        return {"error": f"HTTP error: {e.response.status_code}"}
    except Exception as e:  # noqa: BLE001
        logger.error("GitHub auth API call failed: %s: %s", type(e).__name__, str(e))
        return {"error": f"Auth request failed: {type(e).__name__}"}


async def resolve_github_token_from_email(email: str) -> dict[str, Any]:
    """Resolve a GitHub token for a user identified by email.

    Chains get_ls_user_id_from_email -> get_github_token_for_user.

    Returns:
        Dict with one of:
        - {"token": str} on success
        - {"auth_url": str} if user needs to authenticate via OAuth
        - {"error": str} on failure; error="no_ls_user" if email not in LangSmith
    """
    user_info = await get_ls_user_id_from_email(email)
    ls_user_id = user_info.get("ls_user_id")
    tenant_id = user_info.get("tenant_id")

    if not ls_user_id or not tenant_id:
        logger.warning(
            "No LangSmith user found for email %s (ls_user_id=%s, tenant_id=%s)",
            email,
            ls_user_id,
            tenant_id,
        )
        return {"error": "no_ls_user", "email": email}

    auth_result = await get_github_token_for_user(ls_user_id, tenant_id)
    return auth_result


async def leave_failure_comment(
    source: str,
    message: str,
) -> None:
    """Leave an auth failure comment for the appropriate source."""
    config = get_config()
    configurable = config.get("configurable", {})

    if source == "linear":
        linear_issue = configurable.get("linear_issue", {})
        issue_id = linear_issue.get("id") if isinstance(linear_issue, dict) else None
        if issue_id:
            logger.info(
                "Posting auth failure comment to Linear issue %s (source=%s)",
                issue_id,
                source,
            )
            await comment_on_linear_issue(issue_id, message)
        return
    if source == "slack":
        slack_thread = configurable.get("slack_thread", {})
        channel_id = slack_thread.get("channel_id") if isinstance(slack_thread, dict) else None
        thread_ts = slack_thread.get("thread_ts") if isinstance(slack_thread, dict) else None
        triggering_user_id = (
            slack_thread.get("triggering_user_id") if isinstance(slack_thread, dict) else None
        )
        if channel_id and thread_ts:
            if isinstance(triggering_user_id, str) and triggering_user_id:
                logger.info(
                    "Posting auth failure ephemeral reply to Slack user %s in channel %s thread %s",
                    triggering_user_id,
                    channel_id,
                    thread_ts,
                )
                sent = await post_slack_ephemeral_message(
                    channel_id=channel_id,
                    user_id=triggering_user_id,
                    text=message,
                    thread_ts=thread_ts,
                )
                if sent:
                    return
                logger.warning(
                    "Failed to post ephemeral auth failure reply for Slack user %s; falling back to thread reply",
                    triggering_user_id,
                )
            else:
                logger.warning(
                    "Missing Slack triggering_user_id for auth failure reply; falling back to thread reply",
                )
            logger.info(
                "Posting auth failure reply to Slack channel %s thread %s",
                channel_id,
                thread_ts,
            )
            await post_slack_thread_reply(channel_id, thread_ts, message)
        return
    if source == "github":
        logger.warning(
            "Auth failure for GitHub-triggered run (no token to post comment): %s", message
        )
        return
    raise ValueError(f"Unknown source: {source}")


async def persist_encrypted_github_token(thread_id: str, token: str) -> str:
    """Encrypt a GitHub token and store it on the thread metadata."""
    encrypted = encrypt_token(token)
    await client.threads.update(
        thread_id=thread_id,
        metadata={"github_token_encrypted": encrypted},
    )
    return encrypted


async def save_encrypted_token_from_email(
    email: str | None,
    source: str,
) -> tuple[str, str]:
    """Resolve, encrypt, and store a GitHub token based on user email."""
    config = get_config()
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    if not thread_id:
        raise ValueError("GitHub auth failed: missing thread_id")
    if not email:
        message = (
            "❌ **GitHub Auth Error**\n\n"
            "Failed to authenticate with GitHub: missing_user_email\n\n"
            "Please try again or contact support."
        )
        await leave_failure_comment(source, message)
        raise ValueError("GitHub auth failed: missing user_email")

    user_info = await get_ls_user_id_from_email(email)
    ls_user_id = user_info.get("ls_user_id")
    tenant_id = user_info.get("tenant_id")
    if not ls_user_id or not tenant_id:
        account_label = _source_account_label(source)
        message = (
            "🔐 **GitHub Authentication Required**\n\n"
            f"Could not find a LangSmith account for **{email}**.\n\n"
            "Please ensure this email is invited to the main LangSmith organization. "
            f"If your {account_label} account uses a different email than your LangSmith account, "
            "you may need to update one of them to match.\n\n"
            "Once your email is added to LangSmith, "
            f"{_retry_instruction(source)}"
        )
        await leave_failure_comment(source, message)
        raise ValueError(f"No ls_user_id found from email {email}")

    auth_result = await get_github_token_for_user(ls_user_id, tenant_id)
    auth_url = auth_result.get("auth_url")
    if auth_url:
        work_item_label = _work_item_label(source)
        auth_link_text = _auth_link_text(source, auth_url)
        message = (
            "🔐 **GitHub Authentication Required**\n\n"
            f"To allow the Open SWE agent to work on this {work_item_label}, "
            "please authenticate with GitHub by clicking the link below:\n\n"
            f"{auth_link_text}\n\n"
            f"{_retry_instruction(source)}"
        )
        await leave_failure_comment(source, message)
        raise ValueError("User not authenticated.")

    token = auth_result.get("token")
    if not token:
        error = auth_result.get("error", "unknown")
        logger.error("GitHub token resolution failed: %s", error)
        message = (
            "❌ **GitHub Auth Error**\n\n"
            "Failed to authenticate with GitHub. "
            "Please try again or contact support."
        )
        await leave_failure_comment(source, message)
        raise ValueError(f"No token found: {error}")

    encrypted = await persist_encrypted_github_token(thread_id, token)
    return token, encrypted


async def _resolve_bot_installation_token(thread_id: str) -> tuple[str, str]:
    """Get a GitHub App installation token and persist it for the thread."""
    bot_token = await get_github_app_installation_token()
    if not bot_token:
        raise RuntimeError(
            "Bot-token-only mode is active (LANGSMITH_API_KEY_PROD set without "
            "X_SERVICE_AUTH_JWT_SECRET) but the GitHub App is not configured. "
            "Set GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, and GITHUB_APP_INSTALLATION_ID."
        )
    logger.info(
        "Using GitHub App installation token for thread %s (bot-token-only mode)", thread_id
    )
    encrypted = await persist_encrypted_github_token(thread_id, bot_token)
    return bot_token, encrypted


async def resolve_github_token(config: RunnableConfig, thread_id: str) -> tuple[str, str]:
    """Resolve a GitHub token from the run config based on the source.

    Routes to the correct auth method depending on whether the run was
    triggered from GitHub (login-based) or Linear/Slack (email-based).

    In bot-token-only mode (LANGSMITH_API_KEY_PROD set without
    X_SERVICE_AUTH_JWT_SECRET), the GitHub App installation token is used
    for all operations instead of per-user OAuth tokens.

    Returns:
        (github_token, new_encrypted) tuple.

    Raises:
        RuntimeError: If source is missing or token resolution fails.
    """
    if is_bot_token_only_mode():
        return await _resolve_bot_installation_token(thread_id)

    configurable = config["configurable"]
    source = configurable.get("source")
    if not source:
        logger.error("Missing source for thread %s; cannot route auth failure responses", thread_id)
        raise RuntimeError(f"GitHub auth failed for thread {thread_id}: missing source")

    try:
        if source == "github":
            cached_token, cached_encrypted = await get_github_token_from_thread(thread_id)
            if cached_token and cached_encrypted:
                return cached_token, cached_encrypted
            github_login = configurable.get("github_login")
            email = GITHUB_USER_EMAIL_MAP.get(github_login or "")
            if not email:
                raise ValueError(f"No email mapping found for GitHub user '{github_login}'")
            return await save_encrypted_token_from_email(email, source)
        return await save_encrypted_token_from_email(configurable.get("user_email"), source)
    except ValueError as exc:
        logger.error("GitHub auth failed for thread %s: %s", thread_id, str(exc))
        raise RuntimeError(str(exc)) from exc
