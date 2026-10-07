"""Merge a pull request a human review request settled, as the GitHub App and never as admin."""

import logging
from dataclasses import dataclass
from typing import Literal

import httpx2

from openswe.github.http import GitHubAppUnavailable, GitHubClient, GitHubError, RepoClient
from openswe.github.squash_message import SquashSource
from openswe.human_review.requests import HumanReviewRequest

logger = logging.getLogger(__name__)

_MERGE_PERMISSIONS = {"contents": "write", "pull_requests": "write"}

MergeStatus = Literal[
    "merged",
    "not_ready",
    "needs_approvals",
    "diff_changed",
    "invalidated",
    "closed",
    "refused",
    "error",
]


@dataclass(frozen=True, slots=True)
class MergeResult:
    status: MergeStatus
    message: str


async def merge_pull_request(
    request: HumanReviewRequest, head_sha: str, allowed_methods: list[str], repo: RepoClient
) -> MergeResult:
    """Merge ``head_sha`` with the first method the repository allows; GitHub's answer is final.

    Merges with an App token narrowed to merging this repository when one can be minted,
    else through ``repo``.
    """
    try:
        async with GitHubClient.as_app(
            repo.owner, repo.name, permissions=_MERGE_PERMISSIONS
        ) as scoped:
            return await _merge(
                request, head_sha, allowed_methods, scoped.repo(repo.owner, repo.name)
            )
    except GitHubAppUnavailable:
        logger.info(
            "No merge-scoped App token; merging with the repository's",
            extra={"request_id": str(request.id)},
        )
        return await _merge(request, head_sha, allowed_methods, repo)


async def _merge(
    request: HumanReviewRequest, head_sha: str, allowed_methods: list[str], repo: RepoClient
) -> MergeResult:
    pr = request.pull_request
    methods = allowed_methods or ["merge"]
    payload: dict[str, object] = {"sha": head_sha, "merge_method": methods[0]}
    try:
        if methods[0] == "squash":
            source = await SquashSource.fetch(repo.github.http, pr.owner, pr.repo, pr.number)
            message = source.message() if source is not None else None
            if message is not None:
                payload["commit_message"] = message
        await repo.github.request(
            "PUT", f"repos/{repo.full_name}/pulls/{pr.number}/merge", json=payload
        )
    except GitHubError as refused:
        return MergeResult(
            "refused",
            f"GitHub refused the merge: {refused.message}. Open SWE never bypasses branch "
            "protection; ask a maintainer if the rules need someone else's approval.",
        )
    except httpx2.HTTPError:
        logger.warning(
            "Human review merge request did not complete",
            extra={"request_id": str(request.id), "kind": request.kind},
            exc_info=True,
        )
        return MergeResult("error", "GitHub did not answer the merge request. Try again.")
    return MergeResult("merged", f"Merged {pr.url}.")
