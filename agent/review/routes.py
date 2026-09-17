"""HTTP API for the PR review feature: reviews, review chat, and review styles."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import AliasGenerator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from agent.dashboard.deps import ADMIN_DEP, SESSION_DEP, filter_repo_models_for_user
from agent.dashboard.profiles import get_valid_access_token
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.github.pull_request_status import pull_request_identity
from agent.github.repos import accessible_repo_full_names
from agent.review.analyzer_cron import remove_continual_cron
from agent.review.chat import (
    delete_review_chat_thread,
    get_review_chat,
    list_review_chat_threads,
    proxy_review_chat_commands,
    proxy_review_chat_history,
    proxy_review_chat_state,
    proxy_review_chat_stream_events,
)
from agent.review.enabled_repos import list_enabled_review_repos, set_review_repo_enabled
from agent.review.eval_jobs import get_reviewer_eval_status
from agent.review.reviews import (
    ReviewSummary,
    create_review_comment,
    get_review,
    get_review_diff,
    get_review_summaries,
    list_review_comments,
    list_reviews,
    proxy_pr_image,
    trigger_re_review,
    update_review_comment,
)
from agent.review.style_jobs import (
    cancel_review_style_analysis,
    start_bootstrap_analysis,
    sync_review_style_run_status,
)
from agent.review.styles import (
    REVIEW_STYLES,
    ReviewStyle,
    ReviewStyleCreate,
    ReviewStylePromptUpdate,
    normalize_repo_full_name,
)

router = APIRouter(tags=["review"])

REVIEWS_PAGE_SIZE = 20


class EnabledReviewRepoUpdate(BaseModel):
    full_name: str
    enabled: bool


@router.get("/enabled-review-repos")
async def api_list_enabled_review_repos(
    _session: dict[str, Any] = SESSION_DEP,
) -> dict[str, list[str]]:
    return {"repos": await list_enabled_review_repos()}


@router.put("/enabled-review-repos")
async def api_set_enabled_review_repo(
    update: EnabledReviewRepoUpdate,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, list[str]]:
    repos = await set_review_repo_enabled(update.full_name, update.enabled)
    return {"repos": repos}


@router.get("/admin/evals/reviewer")
async def admin_get_reviewer_eval(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    """Read-only status for the reviewer eval (triggered from the GitHub Action)."""
    return await get_reviewer_eval_status()


@router.get("/review-styles")
async def api_list_review_styles(
    session: dict[str, Any] = SESSION_DEP,
) -> list[ReviewStyle]:
    records = await filter_repo_models_for_user(session["sub"], await REVIEW_STYLES.list_all())
    return [
        await sync_review_style_run_status(record.full_name)
        if record.status == "running"
        else record
        for record in records
    ]


class ReviewSummaryRef(BaseModel):
    repo: str = Field(max_length=140)
    number: int = Field(ge=1)


class ReviewSummariesRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(validation_alias=to_camel), populate_by_name=True
    )

    pull_requests: list[ReviewSummaryRef] = Field(max_length=100)


@router.post("/reviews/summaries")
async def api_get_review_summaries(
    payload: ReviewSummariesRequest,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, ReviewSummary | None]:
    identities: list[tuple[str, str, int]] = []
    for ref in payload.pull_requests:
        identity = pull_request_identity({"repo_full_name": ref.repo, "number": ref.number})
        if identity is None:
            raise HTTPException(422, "invalid pull request reference")
        identities.append(identity)
    accessible = await accessible_repo_full_names(session["sub"])
    authorized = [
        (owner, repo, number)
        for owner, repo, number in identities
        if f"{owner}/{repo}".lower() in accessible
    ]
    return await get_review_summaries(authorized) if authorized else {}


@router.get("/reviews")
async def api_list_reviews(
    page: int = 0,
    mine: bool = True,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    login = session["sub"]
    accessible = await accessible_repo_full_names(login)

    async def is_accessible(summary: dict[str, Any]) -> bool:
        return summary["full_name"].lower() in accessible

    page = max(page, 0)
    reviews, has_more = await list_reviews(
        REVIEWS_PAGE_SIZE,
        offset=page * REVIEWS_PAGE_SIZE,
        author=login if mine else None,
        is_accessible=is_accessible,
    )
    return {"reviews": reviews, "page": page, "has_more": has_more}


@router.get("/reviews/{owner}/{repo}/{pr_number}")
async def api_get_review(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review(owner, repo, pr_number)


@router.get("/reviews/{owner}/{repo}/{pr_number}/diff")
async def api_get_review_diff(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review_diff(owner, repo, pr_number)


@router.get("/reviews/{owner}/{repo}/{pr_number}/image")
async def api_get_review_image(
    owner: str,
    repo: str,
    pr_number: int,
    url: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await proxy_pr_image(owner, repo, pr_number, url)


@router.post("/reviews/{owner}/{repo}/{pr_number}/re-review")
async def api_re_review(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await trigger_re_review(owner, repo, pr_number, session["sub"])


class ReviewCommentCreate(BaseModel):
    path: str
    line: int
    side: Literal["LEFT", "RIGHT"]
    body: str
    start_line: int | None = None
    start_side: Literal["LEFT", "RIGHT"] | None = None


@router.get("/reviews/{owner}/{repo}/{pr_number}/comments")
async def api_list_review_comments(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await list_review_comments(owner, repo, pr_number)


@router.post("/reviews/{owner}/{repo}/{pr_number}/comments")
async def api_create_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment: ReviewCommentCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = comment.body.strip()
    if not body:
        raise HTTPException(422, "comment body is required")
    # Post as the signed-in user (their user-to-server token), so the comment is
    # attributed to them rather than the Open SWE app.
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub re-auth required")
    return await create_review_comment(
        owner,
        repo,
        pr_number,
        token=token,
        path=comment.path,
        line=comment.line,
        side=comment.side,
        body=body,
        start_line=comment.start_line,
        start_side=comment.start_side,
    )


class ReviewCommentUpdate(BaseModel):
    body: str


@router.patch("/reviews/{owner}/{repo}/{pr_number}/comments/{comment_id}")
async def api_update_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment_id: int,
    comment: ReviewCommentUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = comment.body.strip()
    if not body:
        raise HTTPException(422, "comment body is required")
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub re-auth required")
    return await update_review_comment(
        owner,
        repo,
        pr_number,
        comment_id,
        token=token,
        viewer_login=session["sub"],
        body=body,
    )


# --- PR chat (sandbox-less ``chat`` graph) -----------------------------------
# The frontend points a LangGraph StreamProvider at the base
# ``/reviews/{owner}/{repo}/{pr_number}/chat``; the SDK then issues the
# ``/threads/{id}/{commands,stream/events,state,history}`` calls proxied below.


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat")
async def api_get_review_chat(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review_chat(owner, repo, pr_number, session["sub"])


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat/threads")
async def api_list_review_chat_threads(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    threads = await list_review_chat_threads(owner, repo, pr_number, session["sub"])
    return {"threads": threads}


@router.delete("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}")
async def api_delete_review_chat_thread(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    await delete_review_chat_thread(owner, repo, pr_number, session["sub"], thread_id)
    return Response(status_code=204)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/commands")
async def api_review_chat_commands(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = await request.body()
    status_code, content, media_type = await proxy_review_chat_commands(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/stream/events")
async def api_review_chat_stream_events(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION_DEP,
) -> StreamingResponse:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = await request.body()
    stream = await proxy_review_chat_stream_events(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/state")
async def api_review_chat_state(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    status_code, content, media_type = await proxy_review_chat_state(
        owner, repo, pr_number, session["sub"], thread_id
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/history")
async def api_review_chat_history(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = await request.body()
    status_code, content, media_type = await proxy_review_chat_history(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/review-styles")
async def api_create_review_style(
    body: ReviewStyleCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> ReviewStyle:
    await require_repo_access_for_user(session["sub"], body.full_name)
    return await REVIEW_STYLES.create(body.full_name, session["sub"])


@router.get("/review-styles/{full_name:path}")
async def api_get_review_style(
    full_name: str,
    session: dict[str, Any] = SESSION_DEP,
) -> ReviewStyle:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await REVIEW_STYLES.get(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    if record.status == "running":
        record = await sync_review_style_run_status(full_name)
    return record


@router.put("/review-styles/{full_name:path}")
async def api_update_review_style_prompt(
    full_name: str,
    body: ReviewStylePromptUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> ReviewStyle:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    if not await REVIEW_STYLES.get(full_name):
        raise HTTPException(404, "review style not found")
    return await REVIEW_STYLES.set_custom_prompt(full_name, body.custom_prompt)


@router.post("/review-styles/{full_name:path}/analyze")
async def api_analyze_review_style(
    full_name: str,
    session: dict[str, Any] = SESSION_DEP,
) -> ReviewStyle:
    full_name = normalize_repo_full_name(full_name)
    token = await require_repo_access_for_user(session["sub"], full_name)
    record = await REVIEW_STYLES.get(full_name) or await REVIEW_STYLES.create(
        full_name, session["sub"]
    )
    if record.status == "running":
        record = await sync_review_style_run_status(full_name)
        if record.status == "running":
            raise HTTPException(409, "analysis already running")
    return await start_bootstrap_analysis(
        full_name,
        github_token=token,
        created_by=session["sub"],
    )


@router.post("/review-styles/{full_name:path}/cancel")
async def api_cancel_review_style(
    full_name: str,
    session: dict[str, Any] = SESSION_DEP,
) -> ReviewStyle:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    if not await REVIEW_STYLES.get(full_name):
        raise HTTPException(404, "review style not found")
    return await cancel_review_style_analysis(full_name)


@router.delete("/review-styles/{full_name:path}")
async def api_delete_review_style(
    full_name: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await REVIEW_STYLES.get(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    if record.status == "running":
        await cancel_review_style_analysis(full_name)
    await remove_continual_cron(full_name)
    await REVIEW_STYLES.delete(full_name)
    return Response(status_code=204)
