"""Fetching a dashboard thread and asserting the caller may read or act on it."""

from typing import Any

from fastapi import HTTPException

from agent.config import ENV
from agent.dashboard.profiles import get_valid_access_token
from agent.dashboard.threads.summary import _assert_thread_readable
from agent.dashboard.user_mappings import email_for_login
from agent.utils.json_types import ThreadLike, thread_metadata
from agent.utils.thread_ops import langgraph_client


def _agent_version_metadata() -> dict[str, str]:
    revision = ENV.LANGCHAIN_REVISION_ID.optional()
    return {"LANGSMITH_AGENT_VERSION": revision} if revision else {}


async def _resolve_run_email(login: str, profile: dict[str, Any]) -> str | None:
    """Email used for GitHub/LangSmith auth on a run.

    Prefers the admin/self GitHub→email mapping (the work email known to
    the org) over the OAuth profile email, which may be a personal account
    that isn't an org member.
    """
    mapped = await email_for_login(login)
    return mapped or profile.get("email")


async def _ensure_dashboard_github_token(login: str) -> None:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")


# No app-installation-token fallback: PR file contents must be fetched with
# the user's own credential so GitHub enforces their current repo access.
async def _github_token_for_login(login: str) -> str:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    return token


async def _authorized_thread(thread_id: str, login: str, *, email: str | None = None) -> ThreadLike:
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = thread_metadata(thread)
    _assert_thread_readable(metadata)
    return thread


async def _authorized_thread_metadata(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    thread = await _authorized_thread(thread_id, login, email=email)
    metadata = thread_metadata(thread)
    return metadata


async def _readable_thread(
    thread_id: str, *, login: str | None = None, email: str | None = None
) -> ThreadLike:
    """Fetch a thread and assert it is readable by the requesting user.

    Read access is granted to any authenticated org member for surfaced-source
    threads; ``login``/``email`` are accepted for API parity but not required.
    """
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = thread_metadata(thread)
    _assert_thread_readable(metadata)
    return thread


async def _readable_thread_metadata(
    thread_id: str, *, login: str | None = None, email: str | None = None
) -> dict[str, Any]:
    thread = await _readable_thread(thread_id, login=login, email=email)
    metadata = thread_metadata(thread)
    return metadata
