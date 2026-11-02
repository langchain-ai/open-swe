"""Tools: ``review_frontend``, ``read_frontend_review`` and ``submit_frontend_review``.

A pull request's run captures its frontend previews, looks at the screenshots and
publishes what it sees. The run's own repository, pull request and revisions bind
every call; no argument can name another.
"""

import asyncio
import base64
from io import BytesIO
from uuid import UUID

from PIL import Image
from pydantic import JsonValue

from openswe.frontend_review.capture import capture_run, validate_preview_url
from openswe.frontend_review.models import FrontendReviewRun
from openswe.frontend_review.schemas import (
    AuthMode,
    CapturedFrontendReview,
    FrontendInteractionEvidence,
    FrontendJourney,
    FrontendPageResult,
    FrontendReport,
    ReviewAnnotation,
    StartFrontendReview,
)
from openswe.frontend_review.service import FrontendReviews
from openswe.run_config import RunConfig

# What the model sees of each screenshot; annotations use the original page's size.
_THUMBNAIL_SIZE = (864, 600)


class _RunPullRequest:
    """The pull request this run is bound to, which every frontend review call acts on."""

    def __init__(self, cfg: RunConfig) -> None:
        if cfg.repo is None or cfg.pr_number is None or cfg.thread_id is None:
            raise ValueError("Frontend review needs a run bound to a repository and pull request")
        self.evidence = FrontendReviews(cfg.repo.owner, cfg.repo.name, cfg.pr_number)
        self.owner = cfg.repo.owner
        self.repo = cfg.repo.name
        self.number = cfg.pr_number
        self.thread_id = str(cfg.thread_id)
        self.base_sha = cfg.base_sha
        self.head_sha = cfg.head_sha

    def exact_head(self) -> str:
        """The PR head this run was started at: evidence of any other revision is refused."""
        if self.head_sha is None:
            raise ValueError("Frontend evidence needs a run bound to the exact pull request head")
        return self.head_sha

    def report_url(self, review: FrontendReviewRun) -> str:
        """Where people see the capture: the Design QA app's page for the pull request."""
        return f"/agents/plugins/design-qa/{self.owner}/{self.repo}/{self.number}?run={review.id}"

    async def capture(self, request: StartFrontendReview) -> FrontendReviewRun:
        validate_preview_url(request.base_url)
        validate_preview_url(request.head_url)
        review = await self.evidence.create(
            request, expected_base_sha=self.base_sha, expected_head_sha=self.head_sha
        )
        if request.auth_mode != "none":
            return review
        await capture_run(review)
        captured = await self.evidence.get(review.id)
        if captured is None:
            raise RuntimeError("The captured frontend review could not be read")
        if captured.status == "complete" and captured.head_sha == self.head_sha:
            # This thread looked at the screenshots, so its assessment is the one to publish.
            return await self.evidence.set_assessment_state(
                captured, "running", thread_id=self.thread_id
            )
        return captured

    async def owned(self, review_id: UUID) -> FrontendReviewRun:
        """A capture of this PR head whose assessment this thread owns."""
        review = await self.evidence.get(review_id)
        if review is None or review.head_sha != self.exact_head():
            raise ValueError("That capture does not belong to this pull request revision")
        report = FrontendReport.model_validate(review.report) if review.report else None
        if report is None or report.assessment_thread_id != self.thread_id:
            raise ValueError("This thread does not own that capture's assessment")
        return review

    async def content(self, review: FrontendReviewRun) -> list[dict[str, JsonValue]]:
        """The capture as the model reads it: its report, then each screenshot as an image."""
        report = FrontendReport.model_validate(review.report) if review.report else None
        view = CapturedFrontendReview(
            review_id=str(review.id),
            status=review.status,
            report_url=self.report_url(review),
            error=review.error,
            summary=report.summary if report else None,
            ai_review_status=report.ai_review_status if report else "not_run",
            build_verification=report.build_verification if report else "unverified",
            interactions=[
                FrontendInteractionEvidence(
                    scenario_id=scenario.id,
                    title=scenario.title,
                    status=scenario.status,
                    actions=scenario.actions,
                    before_error=scenario.before_error,
                    after_error=scenario.after_error,
                    layout_warnings=scenario.layout_warnings,
                )
                for scenario in (report.scenarios if report else [])
                if scenario.interaction
            ],
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
                for scenario in (report.scenarios if report else [])
                if scenario.metrics is not None
                and scenario.width is not None
                and scenario.height is not None
            ],
        )
        content: list[dict[str, JsonValue]] = [{"type": "text", "text": view.model_dump_json()}]
        for scenario in report.scenarios if report else []:
            for role, name in (("before", scenario.before_url), ("after", scenario.after_url)):
                if name is None:
                    continue
                media = await self.evidence.media(review.id, name)
                if media is None or media.content_type != "image/png":
                    raise RuntimeError("A captured frontend screenshot is unavailable")
                png, width, height = await asyncio.to_thread(self._thumbnail, media.content)
                content.append(
                    {
                        "type": "text",
                        "text": (
                            f"Scenario {scenario.id}: {role} screenshot, displayed at "
                            f"{width}x{height}. Use original page dimensions for annotations."
                        ),
                    }
                )
                content.append({"type": "image", "base64": png, "mime_type": "image/png"})
        return content

    @staticmethod
    def _thumbnail(png: bytes) -> tuple[str, int, int]:
        with Image.open(BytesIO(png)) as image:
            thumbnail = image.convert("RGB")
            thumbnail.thumbnail(_THUMBNAIL_SIZE)
            output = BytesIO()
            thumbnail.save(output, format="PNG")
            return (
                base64.b64encode(output.getvalue()).decode("ascii"),
                thumbnail.width,
                thumbnail.height,
            )


async def review_frontend(
    base_url: str,
    head_url: str,
    paths: list[str] | None = None,
    auth_mode: AuthMode = "dummy",
    journeys: list[FrontendJourney] | None = None,
) -> list[dict[str, JsonValue]]:
    """Implement the `review_frontend` tool."""
    pull = _RunPullRequest(RunConfig.from_runtime())
    request = StartFrontendReview(
        base_url=base_url,
        head_url=head_url,
        paths=paths or ["/"],
        auth_mode=auth_mode,
        journeys=journeys or [],
    )
    return await pull.content(await pull.capture(request))


async def read_frontend_review(review_id: str) -> list[dict[str, JsonValue]]:
    """Implement the `read_frontend_review` tool."""
    pull = _RunPullRequest(RunConfig.from_runtime())
    return await pull.content(await pull.owned(UUID(review_id)))


async def submit_frontend_review(
    review_id: str,
    summary: str,
    annotations_by_scenario: dict[str, list[ReviewAnnotation]] | None = None,
) -> dict[str, JsonValue]:
    """Implement the `submit_frontend_review` tool."""
    pull = _RunPullRequest(RunConfig.from_runtime())
    review = await pull.evidence.assess(
        UUID(review_id),
        expected_head_sha=pull.exact_head(),
        assessment_thread_id=pull.thread_id,
        summary=summary,
        annotations_by_scenario={
            scenario: [ReviewAnnotation.model_validate(annotation) for annotation in annotations]
            for scenario, annotations in (annotations_by_scenario or {}).items()
        },
    )
    return {
        "review_id": str(review.id),
        "report_url": pull.report_url(review),
        "ai_review_status": "complete",
    }
