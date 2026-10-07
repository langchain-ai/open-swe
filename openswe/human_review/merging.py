"""Merge a pull request a human review request settled, as the GitHub App and never as admin."""

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx2

from openswe.expedited_review.reviews import github_error
from openswe.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from openswe.github.http import GITHUB_API_BASE, github_client, github_request
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


async def merge_token(owner: str, repo: str) -> str | None:
    installation_id = await get_github_app_installation_id_for_repo(owner, repo)
    if installation_id is None:
        return None
    return await get_github_app_installation_token(
        installation_id=installation_id,
        repositories=[repo],
        permissions=_MERGE_PERMISSIONS,
    )


async def merge_pull_request(
    request: HumanReviewRequest, head_sha: str, allowed_methods: list[str], token: str
) -> MergeResult:
    """Merge ``head_sha`` with the first method the repository allows; GitHub's answer is final."""
    pr = request.pull_request
    token = await merge_token(pr.owner, pr.repo) or token
    methods = allowed_methods or ["merge"]
    url = f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/merge"
    payload: dict[str, Any] = {"sha": head_sha, "merge_method": methods[0]}
    try:
        async with github_client(token=token) as client:
            if methods[0] == "squash":
                source = await SquashSource.fetch(client, pr.owner, pr.repo, pr.number)
                message = source.message() if source is not None else None
                if message is not None:
                    payload["commit_message"] = message
            response = await github_request(client, "PUT", url, json=payload)
    except httpx2.HTTPError:
        logger.warning(
            "Human review merge request did not complete",
            extra={"request_id": str(request.id), "kind": request.kind},
            exc_info=True,
        )
        return MergeResult("error", "GitHub did not answer the merge request. Try again.")
    if response.status_code == 200:
        return MergeResult("merged", f"Merged {pr.url}.")
    return MergeResult(
        "refused",
        f"GitHub refused the merge: {github_error(response)}. Open SWE never bypasses branch "
        "protection; ask a maintainer if the rules need someone else's approval.",
    )
