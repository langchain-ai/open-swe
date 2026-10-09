"""HTTP API for the PR review feature: reviews, review chat, and review styles."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Self

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import AliasGenerator, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP, filter_repo_models_for_user
from openswe.dashboard.options import model_supports_effort
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient, RepoClient
from openswe.github.pull_request_status import pull_request_identity
from openswe.github.repos import accessible_repo_full_names
from openswe.review.analyzer_cron import remove_continual_cron
from openswe.review.approvals import fetch_approvals_md
from openswe.review.assessment_feedback import (
    AssessmentFeedback,
    FeedbackSubmission,
    feedback_store,
    require_assessment_access,
    save_feedback,
)
from openswe.review.chat import (
    ReviewChat,
    get_review_chat,
    proxy_review_chat_commands,
    proxy_review_chat_history,
    proxy_review_chat_state,
    proxy_review_chat_stream_events,
)
from openswe.review.enabled_repos import list_enabled_review_repos, set_review_repo_enabled
from openswe.review.eval_jobs import (
    ScoreMode,
    Severity,
    get_reviewer_eval_status,
    resolve_eval_config,
    start_reviewer_eval,
)
from openswe.review.labels import LabelChange, PullRequestLabels
from openswe.review.reviews import (
    PendingReview,
    PendingReviewCommentInput,
    PostedReviewComment,
    PullRequestPreview,
    PullRequestReviewEvent,
    ReviewScoutTrigger,
    ReviewSummary,
    SubmittedReview,
    get_pull_request_preview,
    get_review,
    get_review_diff,
    get_review_file_contents,
    get_review_summaries,
    list_reviews,
    proxy_pr_image,
    trigger_re_review,
    trigger_review_scout,
)
from openswe.review.session import ReviewSession
from openswe.review.style_jobs import (
    cancel_review_style_analysis,
    start_bootstrap_analysis,
    sync_review_style_run_status,
)
from openswe.review.styles import (
    REVIEW_STYLES,
    ReviewStyle,
    ReviewStyleCreate,
    ReviewStylePromptUpdate,
    normalize_repo_full_name,
)
from openswe.review.walkthrough import Walkthrough
from openswe.threads.handlers import mark_review_session_viewed

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
@audit_endpoint
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
    """Status of the latest reviewer eval."""
    return await get_reviewer_eval_status()


class ReviewerEvalStart(BaseModel):
    dataset_name: str = Field(min_length=1, max_length=200)
    experiment_prefix: str = Field(min_length=1, max_length=200)
    max_concurrency: int = Field(ge=1, le=50)
    model_id: str
    reasoning_effort: str
    score_mode: ScoreMode
    severity_threshold: Severity
    limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_model_effort(self) -> Self:
        if not model_supports_effort(self.model_id, self.reasoning_effort):
            raise ValueError(f"{self.model_id} does not support effort {self.reasoning_effort}")
        return self


@router.post("/admin/evals/reviewer")
@audit_endpoint
async def admin_start_reviewer_eval(
    body: ReviewerEvalStart,
    session: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    """Launch the reviewer eval in a LangSmith sandbox with the given run config."""
    config = resolve_eval_config()
    config.update(body.model_dump(exclude={"limit"}))
    try:
        return await start_reviewer_eval(config, body.limit, session.get("email") or session["sub"])
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


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


@router.get("/reviews/{owner}/{repo}/{pr_number}/feedback/{review_id}")
async def get_assessment_feedback(
    owner: str,
    repo: str,
    pr_number: int,
    review_id: int,
    session: dict[str, object] = SESSION_DEP,
) -> AssessmentFeedback | None:
    login = str(session["sub"])
    await require_assessment_access(owner, repo, pr_number, review_id, login)
    return await feedback_store(review_id).get(login.lower())


@router.put("/reviews/{owner}/{repo}/{pr_number}/feedback/{review_id}")
@audit_endpoint
async def submit_assessment_feedback(
    owner: str,
    repo: str,
    pr_number: int,
    review_id: int,
    submission: FeedbackSubmission,
    session: dict[str, object] = SESSION_DEP,
) -> AssessmentFeedback:
    return await save_feedback(owner, repo, pr_number, review_id, str(session["sub"]), submission)


@router.get("/reviews/{owner}/{repo}/{pr_number}/preview")
async def api_get_pull_request_preview(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> PullRequestPreview:
    async with _as_viewer(session, owner, repo) as repository:
        return await get_pull_request_preview(repository.pull_request(pr_number))


@router.get("/reviews/{owner}/{repo}/{pr_number}/labels")
async def api_get_pull_request_labels(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> PullRequestLabels:
    async with _as_viewer(session, owner, repo) as repository:
        return await PullRequestLabels.read(repository.pull_request(pr_number))


@router.patch("/reviews/{owner}/{repo}/{pr_number}/labels", status_code=204)
@audit_endpoint
async def api_change_pull_request_label(
    owner: str,
    repo: str,
    pr_number: int,
    change: LabelChange,
    session: dict[str, Any] = SESSION_DEP,
) -> None:
    async with _as_viewer(session, owner, repo) as repository:
        await change.apply(repository.pull_request(pr_number))


@router.get("/reviews/{owner}/{repo}/{pr_number}/diff")
async def api_get_review_diff(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review_diff(owner, repo, pr_number)


@router.get("/reviews/{owner}/{repo}/{pr_number}/file-contents")
async def api_get_review_file_contents(
    owner: str,
    repo: str,
    pr_number: int,
    path: str,
    base_sha: str,
    head_sha: str,
    original_path: str = "",
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, str | None]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review_file_contents(
        owner, repo, pr_number, path, original_path, base_sha, head_sha
    )


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
@audit_endpoint
async def api_re_review(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await trigger_re_review(owner, repo, pr_number, session["sub"])


@router.post("/reviews/{owner}/{repo}/{pr_number}/scout")
@audit_endpoint
async def api_run_review_scout(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> ReviewScoutTrigger:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await trigger_review_scout(owner, repo, pr_number, session["sub"])


@router.post("/reviews/{owner}/{repo}/{pr_number}/viewed", status_code=204)
async def api_mark_review_viewed(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await mark_review_session_viewed(
        ReviewSession(owner=owner, repo=repo, pr_number=pr_number, login=session["sub"])
    )
    return Response(status_code=204)


class WalkthroughDismissed(BaseModel):
    dismissed: bool


@router.delete("/reviews/{owner}/{repo}/{pr_number}/walkthrough")
@audit_endpoint
async def api_dismiss_walkthrough(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> WalkthroughDismissed:
    """Drop the stored walkthrough so the next scout run rebuilds it."""
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return WalkthroughDismissed(dismissed=await Walkthrough.dismiss(owner, repo, pr_number))


@router.post("/reviews/{owner}/{repo}/{pr_number}/comments")
@audit_endpoint
async def api_post_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment: PendingReviewCommentInput,
    session: dict[str, Any] = SESSION_DEP,
) -> PostedReviewComment:
    async with _as_viewer(session, owner, repo) as repository:
        return await PostedReviewComment.post(repository.pull_request(pr_number), comment)


class PullRequestReviewSubmit(BaseModel):
    event: PullRequestReviewEvent
    body: str = Field(default="", max_length=65_000)


@router.post("/reviews/{owner}/{repo}/{pr_number}/submit-review")
@audit_endpoint
async def api_submit_pull_request_review(
    owner: str,
    repo: str,
    pr_number: int,
    review: PullRequestReviewSubmit,
    session: dict[str, Any] = SESSION_DEP,
) -> SubmittedReview:
    async with _as_viewer(session, owner, repo) as repository:
        return await SubmittedReview.submit(
            repository.pull_request(pr_number),
            login=session["sub"],
            event=review.event,
            body=review.body.strip(),
        )


@asynccontextmanager
async def _as_viewer(session: dict[str, Any], owner: str, repo: str) -> AsyncIterator[RepoClient]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    async with GitHubClient.as_user(session["sub"]) as github:
        yield github.repo(owner, repo)


@router.get("/reviews/{owner}/{repo}/{pr_number}/pending-review")
async def api_get_pending_review(
    owner: str, repo: str, pr_number: int, session: dict[str, Any] = SESSION_DEP
) -> PendingReview | None:
    async with _as_viewer(session, owner, repo) as repository:
        return await PendingReview.load(repository.pull_request(pr_number), login=session["sub"])


@router.post("/reviews/{owner}/{repo}/{pr_number}/pending-review/comments")
@audit_endpoint
async def api_add_pending_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment: PendingReviewCommentInput,
    session: dict[str, Any] = SESSION_DEP,
) -> PendingReview:
    async with _as_viewer(session, owner, repo) as repository:
        return await PendingReview.add_comment(
            repository.pull_request(pr_number), comment, login=session["sub"]
        )


class PendingReviewCommentUpdate(BaseModel):
    body: str = Field(max_length=65_000)


@router.patch("/reviews/{owner}/{repo}/{pr_number}/pending-review/comments/{comment_id}")
@audit_endpoint
async def api_update_pending_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment_id: int,
    update: PendingReviewCommentUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> PendingReview:
    async with _as_viewer(session, owner, repo) as repository:
        return await PendingReview.update_comment(
            repository.pull_request(pr_number),
            comment_id,
            update.body.strip(),
            login=session["sub"],
        )


@router.delete("/reviews/{owner}/{repo}/{pr_number}/pending-review/comments/{comment_id}")
@audit_endpoint
async def api_delete_pending_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment_id: int,
    session: dict[str, Any] = SESSION_DEP,
) -> PendingReview | None:
    async with _as_viewer(session, owner, repo) as repository:
        await repository.delete_review_comment(comment_id)
        return await PendingReview.load(repository.pull_request(pr_number), login=session["sub"])


class PendingReviewDiscarded(BaseModel):
    discarded: bool


@router.delete("/reviews/{owner}/{repo}/{pr_number}/pending-review")
@audit_endpoint
async def api_discard_pending_review(
    owner: str, repo: str, pr_number: int, session: dict[str, Any] = SESSION_DEP
) -> PendingReviewDiscarded:
    async with _as_viewer(session, owner, repo) as repository:
        discarded = await PendingReview.discard(
            repository.pull_request(pr_number), login=session["sub"]
        )
    return PendingReviewDiscarded(discarded=discarded)


# --- PR chat (main agent) ---------------------------------------------------
# The frontend points a LangGraph StreamProvider at the base
# ``/reviews/{owner}/{repo}/{pr_number}/chat``; the SDK then issues the
# ``/threads/{id}/{commands,stream/events,state,history}`` calls proxied below.


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat")
async def api_get_review_chat(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> ReviewChat:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review_chat(owner, repo, pr_number, session["sub"], session.get("email"))


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/commands")
@audit_endpoint
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
@audit_endpoint
async def api_create_review_style(
    body: ReviewStyleCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> ReviewStyle:
    await require_repo_access_for_user(session["sub"], body.full_name)
    return await REVIEW_STYLES.create(body.full_name, session["sub"])


class ApprovalsFileStatus(BaseModel):
    found: bool


# Declared before the detail route, whose ``{full_name:path}`` would otherwise swallow the suffix.
@router.get("/review-styles/{full_name:path}/approvals-file")
async def api_get_review_style_approvals_file(
    full_name: str,
    session: dict[str, Any] = SESSION_DEP,
) -> ApprovalsFileStatus:
    full_name = normalize_repo_full_name(full_name)
    token = await require_repo_access_for_user(session["sub"], full_name)
    owner, _, name = full_name.partition("/")
    return ApprovalsFileStatus(
        found=await fetch_approvals_md(owner, name, None, token=token) is not None
    )


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
@audit_endpoint
async def api_update_review_style_prompt(
    full_name: str,
    body: ReviewStylePromptUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> ReviewStyle:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    if not await REVIEW_STYLES.get(full_name):
        raise HTTPException(404, "review style not found")
    if "approval_mode" in body.model_fields_set:
        from openswe.dashboard.deps import require_admin

        require_admin(session)
    return await REVIEW_STYLES.update_prompts(full_name, body)


@router.post("/review-styles/{full_name:path}/analyze")
@audit_endpoint
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
@audit_endpoint
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
@audit_endpoint
async def api_delete_review_style(
    full_name: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await REVIEW_STYLES.get(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    if record.approval_mode is not None:
        from openswe.dashboard.deps import require_admin

        require_admin(session)
    if record.status == "running":
        await cancel_review_style_analysis(full_name)
    await remove_continual_cron(full_name)
    await REVIEW_STYLES.delete(full_name)
    return Response(status_code=204)
