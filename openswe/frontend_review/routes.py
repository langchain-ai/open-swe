import asyncio
from typing import Literal
from uuid import UUID

import httpx2
from fastapi import APIRouter, HTTPException, Query, Response

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import list_open_pull_requests, pull_request_identity
from openswe.users import User

from .schemas import (
    FrontendReviewInbox,
    FrontendReviewInboxItem,
    FrontendReviewList,
    FrontendReviewView,
    StartFrontendReview,
)
from .service import (
    create_review,
    get_review,
    latest_reviews,
    list_reviews,
    media_for_review,
    review_view,
)

router = APIRouter(tags=["frontend-review"])
_PREFIX = "/reviews/{owner}/{repo}/{pr_number}/frontend-reviews"


@router.get("/frontend-reviews/inbox")
async def api_frontend_review_inbox(
    scope: Literal["mine", "review-requested"] = "review-requested",
    page: int = Query(default=1, ge=1, le=50),
    session: dict[str, object] = SESSION_DEP,
) -> FrontendReviewInbox:
    login = session.get("sub")
    if not isinstance(login, str) or not login:
        raise HTTPException(401, "Sign in to view Design QA")
    async with GitHubClient.as_user(login) as github:
        # GitHub's review-requested qualifier includes requests to the user's teams.
        result = await list_open_pull_requests(
            github, login, scope=scope, page=page, lightweight=True
        )
        identities = [
            (pr.repo.split("/")[0], pr.repo.split("/")[1], pr.number) for pr in result.pull_requests
        ]
        runs = {
            (run.owner.lower(), run.repo.lower(), run.pr_number): run
            for run in await latest_reviews(identities)
        }
        semaphore = asyncio.Semaphore(4)

        async def item(index: int) -> FrontendReviewInboxItem | None:
            pr = result.pull_requests[index]
            owner, repo, number = identities[index]
            run = runs.get((owner.lower(), repo.lower(), number))
            if run is None:
                return None
            async with semaphore:
                pull = await github.repo(owner, repo).pull_request(number).pull()
            if pull.get("state") != "open":
                return None
            head = pull.get("head")
            if isinstance(head, dict) and isinstance(head.get("sha"), str):
                pr.head_sha = head["sha"]
            reviewers = pull.get("requested_reviewers")
            teams = pull.get("requested_teams")
            return FrontendReviewInboxItem(
                pr=pr,
                run=review_view(run),
                requested_reviewers=[
                    reviewer["login"]
                    for reviewer in reviewers
                    if isinstance(reviewer, dict) and isinstance(reviewer.get("login"), str)
                ]
                if isinstance(reviewers, list)
                else [],
                requested_teams=[
                    f"{owner}/{team['slug']}"
                    for team in teams
                    if isinstance(team, dict) and isinstance(team.get("slug"), str)
                ]
                if isinstance(teams, list)
                else [],
            )

        try:
            items = await asyncio.gather(*(item(index) for index in range(len(identities))))
        except (httpx2.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Could not refresh Design QA pull request details") from exc
    return FrontendReviewInbox(
        items=[row for row in items if row is not None],
        next_page=result.next_page,
        incomplete=result.incomplete,
    )


async def _authorize(owner: str, repo: str, number: int, session: dict[str, object]) -> str:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "Invalid pull request")
    login = session.get("sub")
    if not isinstance(login, str) or not login:
        raise HTTPException(401, "Sign in to view frontend evidence")
    await require_repo_access_for_user(login, f"{owner}/{repo}")
    return login


@router.get(_PREFIX)
async def api_list_frontend_reviews(
    owner: str, repo: str, pr_number: int, session: dict[str, object] = SESSION_DEP
) -> FrontendReviewList:
    await _authorize(owner, repo, pr_number, session)
    return FrontendReviewList(
        runs=[review_view(run) for run in await list_reviews(owner, repo, pr_number)]
    )


@router.post(_PREFIX, status_code=201)
@audit_endpoint
async def api_start_frontend_review(
    owner: str,
    repo: str,
    pr_number: int,
    body: StartFrontendReview,
    session: dict[str, object] = SESSION_DEP,
) -> FrontendReviewView:
    login = await _authorize(owner, repo, pr_number, session)
    person = await User.for_login("github", login)
    run = await create_review(
        owner, repo, pr_number, body, created_by=person.id if person else None, queue_login=login
    )
    return review_view(run)


@router.get(_PREFIX + "/{run_id}")
async def api_get_frontend_review(
    owner: str,
    repo: str,
    pr_number: int,
    run_id: UUID,
    session: dict[str, object] = SESSION_DEP,
) -> FrontendReviewView:
    await _authorize(owner, repo, pr_number, session)
    run = await get_review(owner, repo, pr_number, run_id)
    if run is None:
        raise HTTPException(404, "Frontend review not found")
    return review_view(run)


@router.get(_PREFIX + "/{run_id}/media/{name}")
async def api_get_frontend_review_media(
    owner: str,
    repo: str,
    pr_number: int,
    run_id: UUID,
    name: str,
    session: dict[str, object] = SESSION_DEP,
) -> Response:
    await _authorize(owner, repo, pr_number, session)
    asset = await media_for_review(owner, repo, pr_number, run_id, name)
    if asset is None:
        raise HTTPException(404, "Frontend evidence not found")
    return Response(
        content=asset.content,
        media_type=asset.content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
