"""Deterministic thread-id derivations.

Every id here is a cross-process routing contract: webhooks, the dashboard and
the reviewer each re-derive the same id from the same external identifiers to
find an existing thread. Changing a formula orphans live threads, so the exact
input strings and namespaces are part of the persisted data model.
"""

import hashlib
import re
import uuid

__all__ = [
    "baby_sit_lock_thread_id",
    "github_issue_thread_id",
    "linear_issue_thread_id",
    "pr_comment_thread_id",
    "review_style_thread_id",
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


def _sha256_uuid(stable_key: str) -> str:
    digest = hashlib.sha256(stable_key.encode()).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def reviewer_thread_id(owner: str, repo: str, pr_number: int) -> str:
    return _url_uuid(f"{owner}/{repo}/pr/{pr_number}/reviewer")


def pr_comment_thread_id(owner: str, repo: str, pr_number: int) -> str:
    """Agent thread for a PR that Open SWE did not branch, keyed by the PR itself."""
    return _url_uuid(f"{owner}/{repo}/pr/{pr_number}")


def review_style_thread_id(owner: str, repo: str) -> str:
    return _url_uuid(f"{owner}/{repo}/review-style")


def slack_thread_id(channel: str, timestamp: str, nonce: str | None = None) -> str:
    return _url_uuid(f"slack:{channel}:{timestamp}:{nonce or ''}")


def baby_sit_lock_thread_id(key: str) -> str:
    return _url_uuid(f"open-swe:baby-sit-lock:{key}")


def linear_issue_thread_id(issue_id: str) -> str:
    return _sha256_uuid(f"linear-issue:{issue_id}")


def github_issue_thread_id(issue_id: str) -> str:
    return _sha256_uuid(f"github-issue:{issue_id}")


def thread_id_from_branch(branch_name: str) -> str | None:
    """Recover the agent thread id Open SWE embeds in the branches it creates."""
    match = _UUID_RE.search(branch_name)
    return match.group(0) if match else None
