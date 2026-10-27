import io
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from openswe.database import postgres
from openswe.database.orm import NOW
from openswe.github.http import GitHubClient
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_requests import PullRequest
from openswe.ui_invalidations.topics import Topic

from .models import FrontendReviewMedia, FrontendReviewRun
from .schemas import (
    ASSET_NAME,
    MEDIA_FIELDS,
    FrontendReport,
    FrontendReviewView,
    MediaType,
    ReviewAnnotation,
    StartFrontendReview,
)

logger = logging.getLogger(__name__)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_SHA = re.compile(r"[0-9a-fA-F]{40,64}")
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_RUN_BYTES = 32 * 1024 * 1024


class FrontendReviewConflict(ValueError):
    """A capture tried to overwrite an already finished run."""


@dataclass(frozen=True)
class EvidenceAsset:
    name: str
    content_type: MediaType
    content: bytes

    def validate(self) -> tuple[int, int] | None:
        if not ASSET_NAME.fullmatch(self.name):
            raise ValueError("Invalid evidence filename")
        if not self.content or len(self.content) > MAX_ASSET_BYTES:
            raise ValueError("Evidence asset exceeds its size limit")
        if self.content_type == "image/png":
            try:
                with Image.open(io.BytesIO(self.content)) as image:
                    if image.format != "PNG" or image.width > 3840 or image.height > 2160:
                        raise ValueError("Evidence must be a bounded PNG screenshot")
                    image.verify()
                    return image.size
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise ValueError("Invalid PNG evidence") from exc
        if self.content_type == "video/webm" and self.content.startswith(b"\x1aE\xdf\xa3"):
            return None
        if self.content_type == "video/mp4" and self.content[4:8] == b"ftyp":
            return None
        raise ValueError("Unsupported evidence format")


async def create_review(
    owner: str,
    repo: str,
    pr_number: int,
    request: StartFrontendReview,
    *,
    created_by: UUID | None = None,
    expected_base_sha: str | None = None,
    expected_head_sha: str | None = None,
) -> FrontendReviewRun:
    async with GitHubClient.as_app(owner, repo) as github:
        payload = await github.repo(owner, repo).pull_request(pr_number).pull()
    base = payload.get("base")
    head = payload.get("head")
    base_sha = base.get("sha") if isinstance(base, dict) else None
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not (
        isinstance(base_sha, str)
        and _SHA.fullmatch(base_sha)
        and isinstance(head_sha, str)
        and _SHA.fullmatch(head_sha)
    ):
        raise ValueError("GitHub did not provide the current base and head commits")
    if (expected_base_sha and expected_base_sha != base_sha) or (
        expected_head_sha and expected_head_sha != head_sha
    ):
        raise ValueError("The pull request changed after this reviewer run started")
    pull = await PullRequest(owner=owner, repo=repo, number=pr_number).ensure()
    run = FrontendReviewRun(
        pull_request_id=pull.id,
        base_sha=base_sha,
        head_sha=head_sha,
        base_url=request.base_url,
        head_url=request.head_url,
        paths=request.paths,
        auth_mode=request.auth_mode,
        status="blocked" if request.auth_mode == "dummy" else "running",
        error=(
            "Dummy authentication cannot access the preview. No login or capture was attempted."
            if request.auth_mode == "dummy"
            else None
        ),
        created_by=created_by,
        updated_by=created_by,
    )
    run.pull_request = pull
    async with postgres.session() as session:
        session.add(run)
        await session.flush()
        await Topic.PULL_REQUESTS.invalidate(session, key=PullRequestKey.of(owner, repo, pr_number))
    return run


def _scoped_query(owner: str, repo: str, pr_number: int) -> Select[FrontendReviewRun]:
    return (
        select(FrontendReviewRun)
        .join(PullRequest)
        .where(
            func.lower(PullRequest.owner) == owner.lower(),
            func.lower(PullRequest.repo) == repo.lower(),
            PullRequest.number == pr_number,
        )
    )


async def _expire_stale(session: AsyncSession, owner: str, repo: str, pr_number: int) -> None:
    stale_ids = _scoped_query(owner, repo, pr_number).with_only_columns(FrontendReviewRun.id)
    expired = await session.scalar(
        update(FrontendReviewRun)
        .where(
            FrontendReviewRun.id.in_(stale_ids),
            FrontendReviewRun.status.in_(["pending", "running"]),
            FrontendReviewRun.created_at < datetime.now(UTC) - timedelta(minutes=15),
        )
        .values(
            status="failed",
            error="The capture was interrupted or timed out. Start a new review.",
            updated_at=NOW,
            updated_by=None,
        )
        .returning(FrontendReviewRun.id)
    )
    stale_assessments = list(
        await session.scalars(
            _scoped_query(owner, repo, pr_number)
            .where(
                FrontendReviewRun.status == "complete",
                FrontendReviewRun.report["ai_review_status"].astext == "running",
                FrontendReviewRun.updated_at < datetime.now(UTC) - timedelta(minutes=15),
            )
            .with_for_update(of=FrontendReviewRun)
        )
    )
    for run in stale_assessments:
        report = FrontendReport.model_validate(run.report)
        report.ai_review_status = "failed"
        report.assessment_error = (
            "The AI review was interrupted or timed out. The captured evidence remains available."
        )
        run.report = _JSON_OBJECT.validate_python(report.model_dump(mode="json"))
        run.updated_by = None
    if expired is not None or stale_assessments:
        await Topic.PULL_REQUESTS.invalidate(session, key=PullRequestKey.of(owner, repo, pr_number))


async def list_reviews(owner: str, repo: str, pr_number: int) -> list[FrontendReviewRun]:
    async with postgres.session() as session:
        await _expire_stale(session, owner, repo, pr_number)
        return list(
            await session.scalars(
                _scoped_query(owner, repo, pr_number)
                .order_by(FrontendReviewRun.created_at.desc())
                .limit(20)
            )
        )


async def get_review(
    owner: str, repo: str, pr_number: int, run_id: UUID
) -> FrontendReviewRun | None:
    async with postgres.session() as session:
        await _expire_stale(session, owner, repo, pr_number)
        return await session.scalar(
            _scoped_query(owner, repo, pr_number).where(FrontendReviewRun.id == run_id)
        )


def _validate_report(
    report: FrontendReport, assets: list[EvidenceAsset], *, complete: bool
) -> None:
    if (
        report.ai_review_status != "not_run"
        or any(s.annotations for s in report.scenarios)
        or report.assessment_thread_id is not None
        or report.assessment_error is not None
    ):
        raise ValueError("Capture cannot publish an AI assessment")
    if len(assets) > 30 or sum(len(asset.content) for asset in assets) > MAX_RUN_BYTES:
        raise ValueError("Frontend evidence exceeds the per-run limit")
    by_name = {asset.name: asset for asset in assets}
    if len(by_name) != len(assets):
        raise ValueError("Evidence filenames must be unique")
    dimensions = {asset.name: asset.validate() for asset in assets}
    referenced: set[str] = set()
    if report.build_verification != "unverified":
        raise ValueError("Preview build verification is not configured")
    if complete and (not report.scenarios or any(s.status != "complete" for s in report.scenarios)):
        raise ValueError("A complete run requires completed capture scenarios")
    if len({scenario.id for scenario in report.scenarios}) != len(report.scenarios):
        raise ValueError("Capture scenario ids must be unique")
    for scenario in report.scenarios:
        for field in MEDIA_FIELDS:
            name = getattr(scenario, field)
            if name is None:
                continue
            if not isinstance(name, str) or name not in by_name:
                raise ValueError("Capture media must reference this run's uploaded evidence")
            asset = by_name[name]
            expected_type = "video/" if "video" in field else "image/"
            if not asset.content_type.startswith(expected_type):
                raise ValueError("Capture media has the wrong content type")
            if expected_type == "image/" and dimensions[name] != (scenario.width, scenario.height):
                raise ValueError("Screenshot dimensions do not match the scenario")
            referenced.add(name)
        if scenario.status == "complete":
            if not all(
                (scenario.before_url, scenario.after_url, scenario.diff_url, scenario.mask_url)
            ):
                raise ValueError("A completed scenario requires paired screenshots, diff, and mask")
            if scenario.width is None or scenario.height is None or scenario.metrics is None:
                raise ValueError("Completed capture dimensions and metrics are required")
            if scenario.metrics.total_pixels != scenario.width * scenario.height:
                raise ValueError("Capture pixel counts do not match its dimensions")
        if scenario.width and scenario.height:
            for rect in [*scenario.regions, *scenario.annotations, *scenario.mask_rects]:
                if rect.x + rect.width > scenario.width or rect.y + rect.height > scenario.height:
                    raise ValueError("Capture regions must fit inside the screenshot")
    if referenced != set(by_name):
        raise ValueError("Every evidence asset must belong to a capture scenario")


async def finish_review(
    run_id: UUID,
    *,
    owner: str,
    repo: str,
    pr_number: int,
    status: Literal["complete", "blocked", "failed"],
    report: FrontendReport | None = None,
    error: str | None = None,
    assets: list[EvidenceAsset] | None = None,
) -> FrontendReviewRun:
    assets = assets or []
    if report is None and (assets or status == "complete"):
        raise ValueError("Captured evidence requires a report")
    if report is not None:
        _validate_report(report, assets, complete=status == "complete")
    async with postgres.session() as session:
        run = await session.scalar(
            _scoped_query(owner, repo, pr_number)
            .where(FrontendReviewRun.id == run_id)
            .with_for_update(of=FrontendReviewRun)
        )
        if run is None:
            raise LookupError("Frontend review not found")
        if run.status not in {"pending", "running"}:
            raise FrontendReviewConflict("Finished frontend reviews are immutable")
        if run.auth_mode == "dummy" and status != "blocked":
            raise ValueError("Dummy authentication cannot produce a completed capture")
        if report is not None:
            captured_paths = [scenario.path for scenario in report.scenarios]
            if not set(captured_paths).issubset(run.paths) or (
                status == "complete" and sorted(captured_paths) != sorted(run.paths)
            ):
                raise ValueError("Capture scenarios must match the requested routes")
        run.status = status
        run.error = error[:3000] if error else None
        run.report = (
            _JSON_OBJECT.validate_python(report.model_dump(mode="json")) if report else None
        )
        run.updated_by = None
        session.add_all(
            [
                FrontendReviewMedia(
                    run_id=run.id, name=a.name, content_type=a.content_type, content=a.content
                )
                for a in assets
            ]
        )
        await session.flush()
        await session.refresh(run, ["updated_at"])
        await Topic.PULL_REQUESTS.invalidate(session, key=PullRequestKey.of(owner, repo, pr_number))
    return run


async def assess_review(
    owner: str,
    repo: str,
    pr_number: int,
    run_id: UUID,
    *,
    expected_head_sha: str,
    summary: str,
    annotations_by_scenario: dict[str, list[ReviewAnnotation]],
    created_by: UUID | None = None,
) -> FrontendReviewRun:
    if not summary.strip() or len(summary) > 12000:
        raise ValueError("An assessment summary is required and must fit the report")
    async with postgres.session() as session:
        run = await session.scalar(
            _scoped_query(owner, repo, pr_number)
            .where(FrontendReviewRun.id == run_id)
            .with_for_update(of=FrontendReviewRun)
        )
        if run is None:
            raise LookupError("Frontend review not found")
        if run.status != "complete" or not run.report:
            raise FrontendReviewConflict("Only completed captures can receive an AI assessment")
        if not expected_head_sha or run.head_sha != expected_head_sha:
            raise FrontendReviewConflict("The assessment does not match the captured head commit")
        report = FrontendReport.model_validate(run.report)
        if report.ai_review_status == "complete":
            raise FrontendReviewConflict("A published frontend assessment cannot be overwritten")
        scenarios = {scenario.id: scenario for scenario in report.scenarios}
        if not annotations_by_scenario.keys() <= scenarios.keys():
            raise ValueError("Assessment annotations must reference captured scenarios")
        for scenario_id, annotations in annotations_by_scenario.items():
            scenario = scenarios[scenario_id]
            if len(annotations) > 100 or scenario.width is None or scenario.height is None:
                raise ValueError("Assessment annotations require bounded capture dimensions")
            for annotation in annotations:
                if (
                    annotation.x + annotation.width > scenario.width
                    or annotation.y + annotation.height > scenario.height
                ):
                    raise ValueError(
                        "Assessment annotations must fit inside the captured screenshot"
                    )
            scenario.annotations = annotations
        report.summary = summary.strip()
        report.ai_review_status = "complete"
        report.assessment_error = None
        run.report = _JSON_OBJECT.validate_python(report.model_dump(mode="json"))
        run.updated_by = created_by
        await session.flush()
        await session.refresh(run, ["updated_at"])
        await Topic.PULL_REQUESTS.invalidate(session, key=PullRequestKey.of(owner, repo, pr_number))
    return run


async def set_assessment_state(
    run: FrontendReviewRun,
    state: Literal["running", "failed"],
    *,
    thread_id: str | None = None,
    error: str | None = None,
) -> FrontendReviewRun:
    async with postgres.session() as session:
        current = await session.scalar(
            _scoped_query(run.owner, run.repo, run.pr_number)
            .where(FrontendReviewRun.id == run.id)
            .with_for_update(of=FrontendReviewRun)
        )
        if current is None:
            raise LookupError("Frontend review not found")
        if current.status != "complete" or current.report is None:
            raise FrontendReviewConflict("Only completed captures can start an AI assessment")
        report = FrontendReport.model_validate(current.report)
        if report.ai_review_status == "complete":
            return current
        report.ai_review_status = state
        if thread_id is not None:
            if len(thread_id) > 200:
                raise ValueError("Assessment thread id exceeds its limit")
            report.assessment_thread_id = thread_id
        report.assessment_error = (
            (
                error
                or "The AI review could not be started. The captured evidence remains available."
            )[:3000]
            if state == "failed"
            else None
        )
        current.report = _JSON_OBJECT.validate_python(report.model_dump(mode="json"))
        current.updated_by = None
        await session.flush()
        await session.refresh(current, ["updated_at"])
        await Topic.PULL_REQUESTS.invalidate(
            session, key=PullRequestKey.of(run.owner, run.repo, run.pr_number)
        )
    return current


async def media_for_review(
    owner: str, repo: str, pr_number: int, run_id: UUID, name: str
) -> FrontendReviewMedia | None:
    if not ASSET_NAME.fullmatch(name):
        return None
    async with postgres.session() as session:
        return await session.scalar(
            select(FrontendReviewMedia)
            .join(FrontendReviewRun)
            .join(PullRequest)
            .where(
                func.lower(PullRequest.owner) == owner.lower(),
                func.lower(PullRequest.repo) == repo.lower(),
                PullRequest.number == pr_number,
                FrontendReviewRun.id == run_id,
                FrontendReviewMedia.name == name,
            )
            .options(undefer(FrontendReviewMedia.content))
        )


def review_view(run: FrontendReviewRun) -> FrontendReviewView:
    report = FrontendReport.model_validate(run.report) if run.report else None
    if report:
        prefix = (
            f"/dashboard/api/reviews/{quote(run.owner, safe='')}/{quote(run.repo, safe='')}/"
            f"{run.pr_number}/frontend-reviews/{run.id}/media/"
        )
        for scenario in report.scenarios:
            for field in MEDIA_FIELDS:
                name = getattr(scenario, field)
                if isinstance(name, str):
                    setattr(scenario, field, prefix + quote(name, safe=""))
    return FrontendReviewView(
        id=run.id,
        status=run.status,
        auth_mode=run.auth_mode,
        base_url=run.base_url,
        head_url=run.head_url,
        paths=run.paths,
        base_sha=run.base_sha,
        head_sha=run.head_sha,
        created_at=run.created_at,
        updated_at=run.updated_at,
        error=run.error,
        report=report,
    )
