"""Human approval for opening a PR as someone else in a shared thread.

When a run is triggered by a person who is not the person the PR is attributed
to — a shared Slack thread where a participant asks the bot to publish as
another participant — the PR author gets the final say, once per thread. The
tool DMs the author a Block Kit card and waits briefly for the decision; a
callback interrupts the active run with it. An author can approve once or store
an "always allow" preference keyed by requester, and a thread with a single
participant never asks.
"""

import hashlib
import logging
from typing import Any
from uuid import uuid4

from langgraph_sdk import get_client

from agent.store import now_iso
from agent.users import User

logger = logging.getLogger(__name__)

PR_APPROVALS_KEY = "pr_approvals"
PR_APPROVAL_PENDING = "pending"
PR_APPROVAL_APPROVED = "approved"
PR_APPROVAL_REJECTED = "rejected"
_TERMINAL_STATUSES = {PR_APPROVAL_APPROVED, PR_APPROVAL_REJECTED}
_MAX_APPROVAL_RECORDS = 20


def pr_approval_fingerprint(*, thread_id: str, author_login: str) -> str:
    """One decision per thread and author; every PR opened as them in the thread shares it."""
    raw = f"{thread_id}|{author_login.lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def get_pr_approvals(thread_id: str) -> dict[str, dict[str, Any]]:
    client = get_client()
    thread = await client.threads.get(thread_id)
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    return _approvals_from_metadata(metadata if isinstance(metadata, dict) else None)


def _approvals_from_metadata(metadata: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = metadata.get(PR_APPROVALS_KEY) if metadata else None
    if not isinstance(raw, dict):
        return {}
    approvals: dict[str, dict[str, Any]] = {}
    for fingerprint, value in raw.items():
        if isinstance(fingerprint, str) and fingerprint and isinstance(value, dict):
            record = dict(value)
            record.setdefault("fingerprint", fingerprint)
            approvals[fingerprint] = record
    return approvals


def _metadata_from_update(update: Any) -> dict[str, Any] | None:
    """The metadata patch a ``threads.update`` mock captured, if it has one."""
    kwargs = getattr(update, "kwargs", None)
    if isinstance(kwargs, dict) and isinstance(kwargs.get("metadata"), dict):
        return kwargs["metadata"]
    args = getattr(update, "args", None)
    if isinstance(args, tuple) and len(args) > 1 and isinstance(args[1], dict):
        return args[1]
    return None


async def ensure_pr_approval_pending(
    thread_id: str,
    *,
    fingerprint: str,
    author_login: str,
    requester_login: str,
    owner: str,
    repo: str,
    head: str,
    title: str,
    base: str,
    draft: bool,
    approval_url: str | None = None,
) -> dict[str, Any]:
    """Create (or return) the pending record for one approval request."""
    approvals = await get_pr_approvals(thread_id)
    existing = approvals.get(fingerprint)
    if existing and existing.get("status") in _TERMINAL_STATUSES:
        return existing

    fields = {
        "author_login": author_login,
        "requester_login": requester_login,
        "owner": owner,
        "repo": repo,
        "head": head,
        "base": base,
        "title": title,
        "draft": draft,
        "approval_url": approval_url,
    }
    if existing and existing.get("status") == PR_APPROVAL_PENDING:
        # Same request seen again (double tool call, retry): keep the original
        # requested_at so the record stays the one the card was posted for.
        return {**existing, **fields}
    record = {
        "fingerprint": fingerprint,
        "status": PR_APPROVAL_PENDING,
        **fields,
        "requested_at": now_iso(),
        "notified": False,
    }
    while len(approvals) >= _MAX_APPROVAL_RECORDS:
        approvals.pop(next(iter(approvals)), None)
    approvals[fingerprint] = record
    await _save_approvals(thread_id, approvals)
    return record


async def decide_pr_approval(
    thread_id: str,
    fingerprint: str,
    *,
    approved: bool,
    actor: str,
    always_allow: bool = False,
) -> dict[str, Any] | None:
    """Record the author's decision on one pending request."""
    approvals = await get_pr_approvals(thread_id)
    record = approvals.get(fingerprint)
    if not record:
        return None
    record["status"] = PR_APPROVAL_APPROVED if approved else PR_APPROVAL_REJECTED
    record["decided_at"] = now_iso()
    record["decided_by"] = actor
    requester = record.get("requester_login")
    if always_allow and isinstance(requester, str) and requester:
        author = str(record.get("author_login") or actor)
        if await User.always_allow_pr_attribution(author, requester) is None:
            logger.warning(
                "No user row to store the PR attribution preference on",
                extra={"author_login": author, "requester_login": requester},
            )
    approvals[fingerprint] = record
    await _save_approvals(thread_id, approvals)
    return record


async def pr_approval_approved(thread_id: str, fingerprint: str) -> bool:
    approvals = await get_pr_approvals(thread_id)
    return approvals.get(fingerprint, {}).get("status") == PR_APPROVAL_APPROVED


async def _save_approvals(thread_id: str, approvals: dict[str, dict[str, Any]]) -> None:
    await get_client().threads.update(
        thread_id=thread_id,
        metadata={PR_APPROVALS_KEY: dict(approvals)},
    )


async def mark_pr_approval_notified(thread_id: str, fingerprint: str) -> None:
    approvals = await get_pr_approvals(thread_id)
    record = approvals.get(fingerprint)
    if not record:
        return
    record["notified"] = True
    record["notified_at"] = now_iso()
    approvals[fingerprint] = record
    await _save_approvals(thread_id, approvals)


def new_approval_id() -> str:
    """Opaque id packed into the Block Kit button value."""
    return uuid4().hex[:12]
