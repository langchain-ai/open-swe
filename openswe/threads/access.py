"""Fetching a dashboard thread and asserting the caller may read or act on it."""

from typing import Any

from fastapi import HTTPException

from openswe.config import ENV
from openswe.dashboard.oauth import DashboardSession
from openswe.dashboard.profiles import get_valid_access_token
from openswe.github.http import github_reauth_message
from openswe.threads.summary import assert_thread_readable
from openswe.users import User
from openswe.utils.json_types import ThreadLike, thread_metadata
from openswe.utils.thread_ops import langgraph_client


def agent_version_metadata() -> dict[str, str]:
    revision = ENV.LANGCHAIN_REVISION_ID.optional()
    return {"LANGSMITH_AGENT_VERSION": revision} if revision else {}


async def resolve_run_email(login: str, profile: dict[str, Any]) -> str | None:
    """Email used for GitHub/LangSmith auth on a run.

    Prefers the admin/self GitHub→email mapping (the work email known to
    the org) over the OAuth profile email, which may be a personal account
    that isn't an org member.
    """
    mapped = await User.email_for_login(login)
    return mapped or profile.get("email")


async def _ensure_dashboard_github_token(login: str) -> None:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, github_reauth_message(login))


async def _authorized_thread(thread_id: str, session: DashboardSession) -> ThreadLike:
    """Fetch a thread and authorize the authenticated caller."""
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    assert_thread_readable(thread_metadata(thread), session)
    return thread


async def _readable_thread_metadata(thread_id: str, session: DashboardSession) -> dict[str, Any]:
    return thread_metadata(await _authorized_thread(thread_id, session))
