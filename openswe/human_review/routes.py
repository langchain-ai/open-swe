"""Dashboard API for asking a repository's Slack review channel to review a pull request."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import pull_request_identity
from openswe.github.repo_files import RepoSettings
from openswe.human_review.card import mention
from openswe.human_review.lifecycle import dismiss_by
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import Origin, RequestResult, request_review
from openswe.slack.client import GitHubPrRef
from openswe.users import User

router = APIRouter(tags=["human-review"])


class HumanReviewRequestBody(BaseModel):
    # Omitted, the card shows the start of the pull request's description.
    inline_summary: str | None = None
    channel: str = ""


class HumanReviewDismissBody(BaseModel):
    reason: str = ""


class HumanReviewDismissResult(BaseModel):
    request_id: str


def _pr_ref(owner: str, repo: str, number: int) -> GitHubPrRef:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    return GitHubPrRef(
        owner=owner,
        repo=repo,
        number=number,
        url=f"https://github.com/{owner}/{repo}/pull/{number}",
    )


class HumanReviewAvailability(BaseModel):
    available: bool


@router.get("/repos/{owner}/{repo}/pulls/{number}/human-review")
async def api_human_review_availability(
    owner: str, repo: str, number: int, session: dict[str, Any] = SESSION_DEP
) -> HumanReviewAvailability:
    _pr_ref(owner, repo, number)
    await require_repo_access_for_user(str(session["sub"]), f"{owner}/{repo}")
    async with GitHubClient.as_app(owner, repo) as github:
        pull = github.repo(owner, repo).pull_request(number)
        sha = await pull.head_sha()
        if sha is None:
            raise HTTPException(404, "Pull request is unavailable")
        settings = await RepoSettings.fetch(pull.repo, ref=sha)
        channel = await settings.channel_for_pr(pull)
    return HumanReviewAvailability(available=bool(channel))


@router.post("/repos/{owner}/{repo}/pulls/{number}/human-review")
async def api_request_human_review(
    owner: str,
    repo: str,
    number: int,
    body: HumanReviewRequestBody | None = None,
    session: dict[str, Any] = SESSION_DEP,
) -> RequestResult:
    pr_ref = _pr_ref(owner, repo, number)
    login = str(session["sub"])
    requester = await User.for_login("github", login)
    if requester is None:
        raise HTTPException(403, "No Open SWE user record for this login")
    await require_repo_access_for_user(login, f"{owner}/{repo}")
    options = body or HumanReviewRequestBody()
    inline_summary = options.inline_summary
    if inline_summary is not None and not inline_summary.strip():
        raise HTTPException(422, "inline_summary must not be blank")
    result = await request_review(
        pr_ref,
        Origin(requester=requester),
        channel=options.channel,
        inline_summary=inline_summary,
    )
    if not result.success:
        raise HTTPException(409, result.error)
    return result


@router.post("/repos/{owner}/{repo}/pulls/{number}/human-review/dismiss")
async def api_dismiss_human_review_request(
    owner: str,
    repo: str,
    number: int,
    body: HumanReviewDismissBody | None = None,
    session: dict[str, Any] = SESSION_DEP,
) -> HumanReviewDismissResult:
    """Take down a pull request's open review request, as its card's Dismiss button does."""
    pr_ref = _pr_ref(owner, repo, number)
    login = str(session["sub"])
    await require_repo_access_for_user(login, f"{owner}/{repo}")
    request = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if request is None:
        raise HTTPException(404, "This pull request has no open review request")
    person = await User.for_login("github", login)
    by = mention(person) if person is not None else f"@{login}"
    if not await dismiss_by(request, by, (body or HumanReviewDismissBody()).reason):
        raise HTTPException(409, "This review request is already closed")
    return HumanReviewDismissResult(request_id=str(request.id))
