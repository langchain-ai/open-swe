"""Approval criteria from a repository's ``.open-swe/APPROVALS.md`` and its approval mode.

The file supplies the criteria; the mode on the repository's review style record
decides whether a positive assessment only comments (``dry_run``) or submits a
GitHub approval (``approve``). Callers read the file at the pull request's base
commit so a pull request cannot rewrite the policy it is judged by.
"""

import logging

from openswe.github.repo_files import fetch_repo_file
from openswe.review.styles import (
    REVIEW_STYLES,
    ApprovalMode,
    RepoFullName,
    effective_approval_mode,
)

logger = logging.getLogger(__name__)

APPROVALS_PATH = ".open-swe/APPROVALS.md"
APPROVALS_MAX_CHARS = 10_000


async def fetch_approvals_md(
    owner: str, repo: str, ref: str | None, *, token: str | None
) -> str | None:
    """``.open-swe/APPROVALS.md`` at ``ref`` (the default branch when ``None``).

    ``None`` when the file is absent, empty, too large, or unreadable.
    """
    return await fetch_repo_file(
        owner, repo, APPROVALS_PATH, ref, token=token, max_chars=APPROVALS_MAX_CHARS
    )


async def approval_mode_for(owner: str, repo: str) -> ApprovalMode:
    """The repository's approval mode; a failed lookup is ``dry_run``, which never approves."""
    try:
        record = await REVIEW_STYLES.get(RepoFullName(f"{owner}/{repo}"))
    except Exception:
        logger.exception("approval mode lookup failed", extra={"repository": f"{owner}/{repo}"})
        return "dry_run"
    return effective_approval_mode(record)


async def approval_policy_for_review(
    owner: str, repo: str, base_sha: str, *, token: str | None
) -> str | None:
    """The criteria a review of a pull request into ``base_sha`` assesses against, if any."""
    if not base_sha or await approval_mode_for(owner, repo) == "off":
        return None
    return await fetch_approvals_md(owner, repo, base_sha, token=token)
