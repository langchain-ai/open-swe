"""Dashboard API for asking a repository's Slack review channel to review a pull request."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.expedited_review.readiness import latest_review_states
from openswe.github.http import GitHubClient, GitHubError, or_none
from openswe.github.pull_request_status import (
    OpenPullRequest,
    OpenPullRequests,
    pull_request_identity,
)
from openswe.github.repo_files import RepoSettings
from openswe.human_review.card import mention
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import Origin, RequestResult, request_review
from openswe.slack.client import GitHubPrRef
from openswe.users import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["human-review"])


@router.get("/review-assignments")
async def api_review_assignments(
    repo: str = "",
    sort: Literal["created", "updated"] = "updated",
    direction: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    session: dict[str, str] = SESSION_DEP,
) -> OpenPullRequests:
    login = session["sub"]
    repositories = {name.lower() for name in repo.split(",")} if repo else set()
    if any(
        pull_request_identity({"repo_full_name": name, "number": 1}) is None
        for name in repositories
    ):
        raise HTTPException(422, "repository must be owner/repo")
    user = await User.for_login("github", login)
    requests = await HumanReviewRequest.assigned_to(user.id) if user is not None else []
    requests = [
        request
        for request in requests
        if not repositories or request.pull_request.repo_full_name.lower() in repositories
    ]
    rows: list[OpenPullRequest] = []
    if requests:
        async with GitHubClient.as_user(login) as github:
            allowed: set[str] = set()
            for full_name in {request.pull_request.repo_full_name for request in requests}:
                owner, name = full_name.split("/", 1)
                try:
                    await github.repo(owner, name).info()
                except GitHubError as exc:
                    if exc.response.status_code not in {403, 404}:
                        raise
                    logger.info(
                        "Review assignment repository no longer accessible",
                        extra={"repository": full_name},
                    )
                else:
                    allowed.add(full_name)
            semaphore = asyncio.Semaphore(4)

            async def pending(request: HumanReviewRequest) -> OpenPullRequest | None:
                pr = request.pull_request
                if pr.repo_full_name not in allowed:
                    return None
                async with semaphore:
                    states = await latest_review_states(
                        github.repo(pr.owner, pr.repo).pull_request(pr.number), pr.author
                    )
                if states is None:
                    raise HTTPException(502, "Could not load assigned reviews from GitHub")
                if any(
                    who.lower() == login.lower() and state in {"APPROVED", "CHANGES_REQUESTED"}
                    for who, state in states.items()
                ):
                    return None
                return OpenPullRequest(
                    repo=pr.repo_full_name,
                    number=pr.number,
                    title=pr.title,
                    created_at=request.created_at.isoformat() if request.created_at else None,
                    updated_at=request.updated_at.isoformat() if request.updated_at else None,
                    details_loading=True,
                )

            rows = [
                row
                for row in await asyncio.gather(*(pending(request) for request in requests))
                if row is not None
            ]
    rows.sort(
        key=lambda row: (
            (row.created_at if sort == "created" else row.updated_at) or "",
            row.repo,
            row.number,
        ),
        reverse=direction == "desc",
    )
    start = (page - 1) * 100
    return OpenPullRequests(
        pull_requests=rows[start : start + 100],
        next_page=page + 1 if start + 100 < len(rows) else None,
        incomplete=False,
        updated_at=datetime.now(UTC).isoformat(),
    )


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
        sha = await or_none(pull.head_sha())
        if sha is None:
            raise HTTPException(404, "Pull request is unavailable")
        settings = await RepoSettings.fetch(pull.repo, ref=sha)
        channel = await settings.channel_for_pr(pull)
    return HumanReviewAvailability(available=bool(channel))


@router.post("/repos/{owner}/{repo}/pulls/{number}/human-review")
@audit_endpoint
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
@audit_endpoint
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
    if not await ReviewCard(request).dismiss_by(by, (body or HumanReviewDismissBody()).reason):
        raise HTTPException(409, "This review request is already closed")
    return HumanReviewDismissResult(request_id=str(request.id))
