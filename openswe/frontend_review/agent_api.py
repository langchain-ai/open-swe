"""Named MDA endpoint; the verified run token supplies the repository and PR."""

import asyncio
import base64
from io import BytesIO

from PIL import Image
from runtime_api.endpoint import Handlers
from runtime_api.frontend_review import (
    CAPTURE_FRONTEND_REVIEW,
    READ_FRONTEND_REVIEW,
    SUBMIT_FRONTEND_REVIEW,
    CapturedFrontendReview,
    CaptureFrontendReview,
    FrontendPageResult,
    FrontendScreenshot,
    ReadFrontendReview,
    SubmitFrontendReview,
    SubmittedFrontendReview,
)

from openswe.agent_api.server import RefusedCallError
from openswe.agent_api.tokens import RemoteRun
from openswe.run_config import RunConfig

from .capture import capture_run, validate_preview_url
from .models import FrontendReviewRun
from .schemas import FrontendReport, ReviewAnnotation, StartFrontendReview
from .service import (
    FrontendReviewAccessDenied,
    assess_review,
    create_review,
    get_review,
    media_for_review,
    set_assessment_state,
)

API: Handlers[RemoteRun] = Handlers()


def _thumbnail(content: bytes) -> tuple[str, int, int]:
    with Image.open(BytesIO(content)) as image:
        thumbnail = image.convert("RGB")
        thumbnail.thumbnail((864, 600))
        output = BytesIO()
        thumbnail.save(output, format="PNG")
        return (
            base64.b64encode(output.getvalue()).decode("ascii"),
            thumbnail.width,
            thumbnail.height,
        )


async def _screenshots(
    review: FrontendReviewRun, report: FrontendReport
) -> list[FrontendScreenshot]:
    screenshots: list[FrontendScreenshot] = []
    for scenario in report.scenarios:
        for role, name in (("before", scenario.before_url), ("after", scenario.after_url)):
            if name is None:
                continue
            media = await media_for_review(
                review.owner, review.repo, review.pr_number, review.id, name
            )
            if media is None or media.content_type != "image/png":
                raise RuntimeError("Captured frontend screenshot is unavailable")
            png, width, height = await asyncio.to_thread(_thumbnail, media.content)
            screenshots.append(
                FrontendScreenshot(
                    scenario_id=scenario.id,
                    role=role,
                    width=width,
                    height=height,
                    png_base64=png,
                )
            )
    return screenshots


async def capture(run: RemoteRun, request: CaptureFrontendReview) -> CapturedFrontendReview:
    cfg = RunConfig.parse(run.configurable)
    if cfg.frontend_review_id is not None:
        raise RefusedCallError("A dedicated visual assessment cannot recapture its evidence")
    if run.assistant_id not in {"agent", "reviewer"} or cfg.repo is None or cfg.pr_number is None:
        raise RefusedCallError(
            "Frontend review requires a run bound to a repository and pull request"
        )
    body = StartFrontendReview.model_validate(request.request.model_dump())
    validate_preview_url(body.base_url)
    validate_preview_url(body.head_url)
    review = await create_review(
        cfg.repo.owner,
        cfg.repo.name,
        cfg.pr_number,
        body,
        expected_base_sha=cfg.base_sha,
        expected_head_sha=cfg.head_sha,
    )
    if body.auth_mode == "none":
        await capture_run(review)
        latest = await get_review(cfg.repo.owner, cfg.repo.name, cfg.pr_number, review.id)
        if latest is None:
            raise RuntimeError("Captured frontend review could not be read")
        review = latest
        if review.status == "complete" and cfg.head_sha == review.head_sha:
            review = await set_assessment_state(review, "running", thread_id=run.thread_id)
    return await _view(review)


async def _view(review: FrontendReviewRun) -> CapturedFrontendReview:
    report = FrontendReport.model_validate(review.report) if review.report else None
    return CapturedFrontendReview(
        review_id=str(review.id),
        status=review.status,
        report_url=f"/agents/reviews/{review.owner}/{review.repo}/{review.pr_number}?view=frontend&run={review.id}",
        error=review.error,
        summary=report.summary if report else None,
        ai_review_status=report.ai_review_status if report else "not_run",
        build_verification=report.build_verification if report else "unverified",
        pages=[
            FrontendPageResult(
                id=scenario.id,
                path=scenario.path,
                width=scenario.width,
                height=scenario.height,
                changed_pixels=scenario.metrics.changed_pixels,
                total_pixels=scenario.metrics.total_pixels,
                changed_ratio=scenario.metrics.changed_ratio,
            )
            for scenario in report.scenarios
            if scenario.metrics is not None
            and scenario.width is not None
            and scenario.height is not None
        ]
        if report
        else [],
        screenshots=await _screenshots(review, report) if report else [],
    )


async def inspect(run: RemoteRun, request: ReadFrontendReview) -> CapturedFrontendReview:
    cfg = RunConfig.parse(run.configurable)
    if cfg.frontend_review_id is not None and cfg.frontend_review_id != str(
        request.request.review_id
    ):
        raise RefusedCallError("This visual assessment can inspect only its assigned evidence")
    if (
        run.assistant_id not in {"agent", "reviewer"}
        or cfg.repo is None
        or cfg.pr_number is None
        or cfg.head_sha is None
    ):
        raise RefusedCallError("Frontend evidence requires a run bound to the exact PR head")
    review = await get_review(
        cfg.repo.owner, cfg.repo.name, cfg.pr_number, request.request.review_id
    )
    if review is None or review.head_sha != cfg.head_sha:
        raise RefusedCallError("Frontend evidence does not belong to this PR revision")
    report = FrontendReport.model_validate(review.report) if review.report else None
    if report is None or report.assessment_thread_id != run.thread_id:
        raise RefusedCallError("This thread does not own the frontend assessment")
    return await _view(review)


async def submit(run: RemoteRun, request: SubmitFrontendReview) -> SubmittedFrontendReview:
    cfg = RunConfig.parse(run.configurable)
    if cfg.frontend_review_id is not None and cfg.frontend_review_id != str(
        request.request.review_id
    ):
        raise RefusedCallError("This visual assessment can publish only its assigned evidence")
    if (
        run.assistant_id not in {"agent", "reviewer"}
        or cfg.repo is None
        or cfg.pr_number is None
        or cfg.head_sha is None
    ):
        raise RefusedCallError("Frontend assessment requires a run bound to the exact PR head")
    body = request.request
    try:
        review = await assess_review(
            cfg.repo.owner,
            cfg.repo.name,
            cfg.pr_number,
            body.review_id,
            expected_head_sha=cfg.head_sha,
            assessment_thread_id=run.thread_id,
            summary=body.summary,
            annotations_by_scenario={
                scenario: [
                    ReviewAnnotation.model_validate(annotation.model_dump())
                    for annotation in annotations
                ]
                for scenario, annotations in body.annotations_by_scenario.items()
            },
        )
    except FrontendReviewAccessDenied as exc:
        raise RefusedCallError(str(exc)) from exc
    return SubmittedFrontendReview(
        review_id=review.id,
        report_url=f"/agents/reviews/{cfg.repo.owner}/{cfg.repo.name}/{cfg.pr_number}?view=frontend&run={review.id}",
    )


API.serve(CAPTURE_FRONTEND_REVIEW, capture)
API.serve(READ_FRONTEND_REVIEW, inspect)
API.serve(SUBMIT_FRONTEND_REVIEW, submit)
