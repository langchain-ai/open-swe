"""Dashboard API for asking a repository's Slack review channel to review a pull request."""

from typing import Any

from fastapi import APIRouter, HTTPException

from agent.dashboard.deps import SESSION_DEP
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.github.pull_request_status import pull_request_identity
from agent.human_review.standard import Origin, RequestResult, request_review
from agent.slack.client import GitHubPrRef
from agent.users import User

router = APIRouter(tags=["human-review"])


@router.post("/repos/{owner}/{repo}/pulls/{number}/human-review")
async def api_request_human_review(
    owner: str, repo: str, number: int, session: dict[str, Any] = SESSION_DEP
) -> RequestResult:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    login = str(session["sub"])
    await require_repo_access_for_user(login, f"{owner}/{repo}")
    pr_ref = GitHubPrRef(
        owner=owner,
        repo=repo,
        number=number,
        url=f"https://github.com/{owner}/{repo}/pull/{number}",
    )
    result = await request_review(pr_ref, Origin(requester=await User.for_login("github", login)))
    if not result.success:
        raise HTTPException(409, result.error)
    return result
