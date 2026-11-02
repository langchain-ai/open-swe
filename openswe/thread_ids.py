"""Deterministic thread-id derivations.

Every id here is a cross-process routing contract: webhooks, the dashboard and
the reviewer each re-derive the same id from the same external identifiers to
find an existing thread. Changing a formula orphans live threads, so the exact
input strings and namespaces are part of the persisted data model.
"""

import re
import uuid

__all__ = [
    "mda_successor_thread_id",
    "pr_comment_thread_id",
    "review_chat_thread_id",
    "review_shadow_thread_id",
    "reviewer_thread_id",
    "slack_thread_id",
    "thread_id_from_branch",
]

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def _url_uuid(stable_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key))


def reviewer_thread_id(owner: str, repo: str, pr_number: int) -> str:
    return _url_uuid(f"{owner}/{repo}/pr/{pr_number}/reviewer")


def review_scout_thread_id(owner: str, repo: str, pr_number: int) -> str:
    return _url_uuid(f"{owner}/{repo}/pr/{pr_number}/review-scout")


def review_shadow_thread_id(owner: str, repo: str, pr_number: int) -> str:
    return _url_uuid(f"{owner}/{repo}/pr/{pr_number}/reviewer-shadow")


def pr_comment_thread_id(owner: str, repo: str, pr_number: int) -> str:
    """Agent thread for a PR that Open SWE did not branch, keyed by the PR itself."""
    return _url_uuid(f"{owner}/{repo}/pr/{pr_number}")


def review_chat_thread_id(owner: str, repo: str, pr_number: int, login: str) -> str:
    """The one chat a user has about a PR. Per-user: chats are not shared."""
    return _url_uuid(f"{owner}/{repo}/pr/{pr_number}/chat/{login.lower()}")


def slack_thread_id(channel: str, timestamp: str, nonce: str | None = None) -> str:
    return _url_uuid(f"slack:{channel}:{timestamp}:{nonce or ''}")


def mda_successor_thread_id(thread_id: str) -> str:
    """The thread that takes new activity for one made before Managed Deep Agents."""
    return _url_uuid(f"thread:{thread_id}:mda")


def thread_id_from_branch(branch_name: str) -> str | None:
    """Recover the agent thread id Open SWE embeds in the branches it creates."""
    match = _UUID_RE.search(branch_name)
    return match.group(0) if match else None
