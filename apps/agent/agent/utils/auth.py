"""GitHub OAuth and LangSmith authentication utilities."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

logger = logging.getLogger(__name__)

LANGSMITH_API_KEY = os.environ.get("LANGSMITH_API_KEY_PROD", "")
LANGSMITH_API_URL = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
LANGSMITH_HOST_API_URL = os.environ.get("LANGSMITH_HOST_API_URL", "https://api.host.langchain.com")
GITHUB_OAUTH_PROVIDER_ID = os.environ.get("GITHUB_OAUTH_PROVIDER_ID", "")
X_SERVICE_AUTH_JWT_SECRET = os.environ.get("X_SERVICE_AUTH_JWT_SECRET", "")

logger.info(
    "Auth env snapshot: LANGSMITH_API_KEY_PROD=%s LANGSMITH_ENDPOINT=%s "
    "LANGSMITH_HOST_API_URL=%s GITHUB_OAUTH_PROVIDER_ID=%s X_SERVICE_AUTH_JWT_SECRET=%s",
    "set" if LANGSMITH_API_KEY else "missing",
    "set" if LANGSMITH_API_URL else "missing",
    "set" if LANGSMITH_HOST_API_URL else "missing",
    "set" if GITHUB_OAUTH_PROVIDER_ID else "missing",
    "set" if X_SERVICE_AUTH_JWT_SECRET else "missing",
)


def get_service_jwt_token_for_user(
    user_id: str, tenant_id: str, expiration_seconds: int = 300
) -> str:
    """Create a short-lived service JWT for authenticating as a specific user."""
    if not X_SERVICE_AUTH_JWT_SECRET:
        msg = "X_SERVICE_AUTH_JWT_SECRET is not configured. Cannot generate service keys."
        raise ValueError(msg)

    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(seconds=expiration_seconds),
    }
    return jwt.encode(payload, X_SERVICE_AUTH_JWT_SECRET, algorithm="HS256")


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
        service_token = get_service_jwt_token_for_user(ls_user_id, tenant_id)

        headers = {
            "X-Service-Key": service_token,
            "X-Tenant-Id": tenant_id,
        }

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
            return {"error": f"Unexpected auth result: {response_data}"}

    except httpx.HTTPStatusError as e:
        logger.error("GitHub auth API HTTP error: %s - %s", e.response.status_code, e.response.text)
        return {"error": f"HTTP error: {e.response.status_code} - {e.response.text}"}
    except Exception as e:  # noqa: BLE001
        logger.error("GitHub auth API call failed: %s: %s", type(e).__name__, str(e))
        return {"error": str(e)}


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
