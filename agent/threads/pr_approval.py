"""Human approval for opening a PR as someone else in a shared thread.

When a run is triggered by a person who is not the person the PR is attributed
to — a shared Slack thread where a participant asks the bot to publish as
another participant — the PR author gets the final say. The tool returns a
``pr_approval_required`` payload instead of blocking the run, posts a Block Kit
card in the Slack thread, and a callback interrupts the active run with the
author's decision so the bot can retry the exact tool call. An author can
approve once or store an "always allow" preference keyed by requester, and a
thread with a single participant never asks.
"""

import hashlib
import logging
from typing import Any, Literal
from uuid import uuid4

from langgraph_sdk import get_client

from agent.store import get_value, now_iso, put_value

logger = logging.getLogger(__name__)

PR_APPROVALS_KEY = "pr_approvals"
PR_APPROVAL_PENDING = "pending"
PR_APPROVAL_APPROVED = "approved"
PR_APPROVAL_REJECTED = "rejected"
_TERMINAL_STATUSES = {PR_APPROVAL_APPROVED, PR_APPROVAL_REJECTED}
_MAX_APPROVAL_RECORDS = 20

PR_APPROVALS_NAMESPACE: list[str] = ["pr_authorization", "user_preferences"]

ALWAYS_ALLOW_ALL = "all"
ALWAYS_ALLOW_NONE = "none"


def pr_approval_fingerprint(
    *,
    thread_id: str,
    author_login: str,
    requester_login: str,
    owner: str,
    repo: str,
    head: str,
) -> str:
    """Identity of one approval request; a retried open gets a fresh one."""
    raw = f"{thread_id}|{author_login}|{requester_login}|{owner}/{repo}|{head}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class AlwaysAllowPreference:
    """A stored per-user decision to skip the approval prompt."""

    def __init__(self, scope: str, requester: str | None = None) -> None:
        self.scope = scope
        self.requester = requester


async def get_always_allow(login: str) -> AlwaysAllowPreference | None:
    value = await get_value(PR_APPROVALS_NAMESPACE, f"always_allow:{login.lower()}")
    if not isinstance(value, dict):
        return None
    scope = value.get("scope")
    requester = value.get("requester")
    requester_login = requester if isinstance(requester, str) and requester.strip() else None
    if scope == ALWAYS_ALLOW_ALL:
        return AlwaysAllowPreference(ALWAYS_ALLOW_ALL)
    if scope == "requester" and requester_login:
        return AlwaysAllowPreference("requester", requester_login)
    return None


async def set_always_allow(
    login: str,
    *,
    allow: bool,
    requester: str | None = None,
) -> AlwaysAllowPreference | None:
    """Store (or clear) the preference; callers persist no record when clearing."""
    key = f"always_allow:{login.lower()}"
    if not allow:
        await put_value(
            PR_APPROVALS_NAMESPACE,
            key,
            {"scope": ALWAYS_ALLOW_NONE, "login": login, "updated_at": now_iso()},
        )
        return None
    preference = AlwaysAllowPreference(
        "requester" if requester else ALWAYS_ALLOW_ALL,
        requester.strip().lower() if requester else None,
    )
    await put_value(
        PR_APPROVALS_NAMESPACE,
        key,
        {
            "scope": preference.scope,
            "requester": preference.requester,
            "login": login,
            "updated_at": now_iso(),
        },
    )
    return preference


async def always_allow_for(
    author_login: str, requester_login: str
) -> Literal["all", "requester", "none"]:
    """Whether ``author_login`` already allows ``requester_login`` to publish as them."""
    preference = await get_always_allow(author_login)
    if preference is None:
        return "none"
    if preference.scope == ALWAYS_ALLOW_ALL:
        return "all"
    if preference.scope == "requester" and preference.requester == requester_login.lower():
        return "requester"
    return "none"


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
    if always_allow:
        requester = record.get("requester_login")
        await set_always_allow(
            str(record.get("author_login") or actor),
            allow=True,
            requester=requester if isinstance(requester, str) else None,
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
