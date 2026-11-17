"""Fetching a dashboard thread and asserting the caller may read or act on it."""

from typing import Any

from fastapi import HTTPException

from openswe.config import ENV
from openswe.threads.identity import ThreadIdentity
from openswe.threads.index import IndexedThread
from openswe.threads.store import THREADS
from openswe.threads.summary import assert_thread_readable
from openswe.users import User
from openswe.utils.json_types import ThreadLike


def agent_version_metadata() -> dict[str, str]:
    revision = ENV.LANGCHAIN_REVISION_ID.optional()
    return {"LANGSMITH_AGENT_VERSION": revision} if revision else {}


async def resolve_run_email(login: str, profile: dict[str, Any]) -> str | None:
    """Email used for GitHub/LangSmith auth on a run.

    Prefers the admin/self GitHub→email mapping (the work email known to
    the org) over the OAuth profile email, which may be a personal account
    that isn't an org member.
    """
    if ThreadIdentity(login).user_id is not None:
        return profile.get("email")
    mapped = await User.email_for_login(login)
    return mapped or profile.get("email")


async def _ensure_dashboard_github_token(login: str) -> None:
    await ThreadIdentity(login).authorize()


async def _authorized_thread(thread_id: str, login: str, *, email: str | None = None) -> ThreadLike:
    """The thread as the Agent Server serves it, once the caller may read it."""
    thread = await _readable_thread(thread_id, login=login, email=email)
    return thread.as_langgraph()


async def _authorized_thread_metadata(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    thread = await _readable_thread(thread_id, login=login, email=email)
    return thread.metadata


async def _readable_thread(
    thread_id: str, *, login: str | None = None, email: str | None = None
) -> IndexedThread:
    """Read a thread from the index and authorize the authenticated caller."""
    thread = await THREADS.get(thread_id)
    if thread is None:
        raise HTTPException(404, "thread not found")
    assert_thread_readable(thread.metadata, login, email)
    return thread


async def _readable_thread_metadata(
    thread_id: str, *, login: str | None = None, email: str | None = None
) -> dict[str, Any]:
    thread = await _readable_thread(thread_id, login=login, email=email)
    return thread.metadata
