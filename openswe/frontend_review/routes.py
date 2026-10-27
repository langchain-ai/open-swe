import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.pull_request_status import pull_request_identity
from openswe.users import User

from .schemas import FrontendReviewList, FrontendReviewView, StartFrontendReview
from .service import (
    create_review,
    finish_review,
    get_review,
    list_reviews,
    media_for_review,
    review_view,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["frontend-review"])
_PREFIX = "/reviews/{owner}/{repo}/{pr_number}/frontend-reviews"


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
        owner, repo, pr_number, body, created_by=person.id if person else None
    )
    if run.auth_mode != "dummy":
        from .capture import capture_run

        try:
            await capture_run(run)
        except Exception:
            logger.exception("Frontend capture failed", extra={"frontend_review_id": str(run.id)})
            await finish_review(
                run.id,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                status="failed",
                error="The capture failed. Check the server logs for details.",
            )
        run = await get_review(owner, repo, pr_number, run.id) or run
        if run.status == "complete":
            from .launch import launch_assessment

            await launch_assessment(run, login)
            run = await get_review(owner, repo, pr_number, run.id) or run
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
