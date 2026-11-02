import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import update

from openswe.database import postgres
from openswe.frontend_review import service
from openswe.frontend_review.models import FrontendReviewRun
from openswe.frontend_review.schemas import (
    DifferenceMetrics,
    FrontendReport,
    FrontendScenario,
    StartFrontendReview,
)
from openswe.frontend_review.service import EvidenceAsset, FrontendReviewConflict
from openswe.github.http import GitHubClient
from openswe.run_config import RunConfig
from openswe.tools import frontend_review as frontend_review_tools


def _assets() -> list[EvidenceAsset]:
    output = io.BytesIO()
    Image.new("RGBA", (2, 1), (226, 61, 117, 255)).save(output, format="PNG")
    return [
        EvidenceAsset(name, "image/png", output.getvalue())
        for name in ("before.png", "after.png", "diff.png", "mask.png")
    ]


def _report() -> FrontendReport:
    return FrontendReport(
        summary="Paired screenshots captured; AI review has not run.",
        scenarios=[
            FrontendScenario(
                id="page-1",
                title="Home",
                path="/",
                status="complete",
                width=2,
                height=1,
                before_url="before.png",
                after_url="after.png",
                diff_url="diff.png",
                mask_url="mask.png",
                metrics=DifferenceMetrics(changed_pixels=1, total_pixels=2, changed_ratio=0.5),
            )
        ],
    )


class _GitHub:
    def repo(self, owner: str, repo: str) -> _GitHub:
        return self

    def pull_request(self, number: int) -> _GitHub:
        return self

    async def pull(self) -> dict[str, object]:
        return {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}


@asynccontextmanager
async def _github(owner: str, repo: str) -> AsyncIterator[_GitHub]:
    yield _GitHub()


@pytest.mark.usefixtures("registry_db")
async def test_evidence_is_pr_scoped_and_finished_runs_cannot_be_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    evidence = service.FrontendReviews("acme", "repo", 7)
    run = await evidence.create(
        StartFrontendReview(
            base_url="https://base.vercel.app", head_url="https://head.vercel.app", auth_mode="none"
        )
    )
    assert (run.base_sha, run.head_sha) == ("a" * 40, "b" * 40)
    await evidence.finish(run.id, status="complete", report=_report(), assets=_assets())
    restored = await evidence.get(run.id)
    assert restored is not None and restored.status == "complete"
    assert restored.view().report is not None
    assert await service.FrontendReviews("acme", "repo", 8).get(run.id) is None
    assert await service.FrontendReviews("other", "repo", 7).media(run.id, "before.png") is None
    media = await service.FrontendReviews("ACME", "REPO", 7).media(run.id, "before.png")
    assert media is not None and media.content == _assets()[0].content
    await evidence.set_assessment_state(restored, "running", thread_id="review-thread")
    with pytest.raises(FrontendReviewConflict, match="head commit"):
        await evidence.assess(
            run.id,
            expected_head_sha="c" * 40,
            assessment_thread_id="review-thread",
            summary="Outdated review",
            annotations_by_scenario={},
        )
    annotated = await evidence.assess(
        run.id,
        expected_head_sha=run.head_sha,
        assessment_thread_id="review-thread",
        summary="Reviewed evidence",
        annotations_by_scenario={},
    )
    assert annotated.report is not None and annotated.report["ai_review_status"] == "complete"
    with pytest.raises(FrontendReviewConflict, match="cannot be overwritten"):
        await evidence.assess(
            run.id,
            expected_head_sha=run.head_sha,
            assessment_thread_id="review-thread",
            summary="Overwrite",
            annotations_by_scenario={},
        )
    with pytest.raises(FrontendReviewConflict):
        await evidence.finish(run.id, status="failed")


@pytest.mark.usefixtures("registry_db")
async def test_dummy_auth_is_terminal_and_stale_agent_revisions_never_create_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    body = StartFrontendReview(
        base_url="https://base.vercel.app", head_url="https://head.vercel.app"
    )
    with pytest.raises(ValueError, match="changed after"):
        await service.FrontendReviews("acme", "repo", 7).create(body, expected_head_sha="c" * 40)
    assert await service.FrontendReviews("acme", "repo", 7).list_runs() == []
    run = await service.FrontendReviews("acme", "repo", 7).create(body)
    assert run.status == "blocked" and run.report is None
    assert run.error and "No login or capture was attempted" in run.error


@pytest.mark.usefixtures("registry_db")
async def test_interrupted_captures_expire_only_within_the_requested_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    request = StartFrontendReview(
        base_url="https://base.vercel.app", head_url="https://head.vercel.app", auth_mode="none"
    )
    first = await service.FrontendReviews("acme", "repo", 7).create(request)
    second = await service.FrontendReviews("acme", "repo", 8).create(request)
    third = await service.FrontendReviews("acme", "repo", 9).create(request)
    completed = await service.FrontendReviews("acme", "repo", 9).finish(
        third.id, status="complete", report=_report(), assets=_assets()
    )
    await service.FrontendReviews(
        completed.owner, completed.repo, completed.pr_number
    ).set_assessment_state(completed, "running", thread_id="interrupted-ai-thread")
    async with postgres.session() as session:
        await session.execute(
            update(FrontendReviewRun).values(
                created_at=datetime.now(UTC) - timedelta(minutes=16),
                updated_at=datetime.now(UTC) - timedelta(minutes=16),
            )
        )
    [expired] = await service.FrontendReviews("acme", "repo", 7).list_runs()
    assert expired.status == "failed" and expired.error and "interrupted" in expired.error
    async with postgres.session() as session:
        untouched = await session.get(FrontendReviewRun, second.id)
        assert untouched is not None and untouched.status == "running"
        untouched_ai = await session.get(FrontendReviewRun, third.id)
        assert untouched_ai is not None and untouched_ai.report is not None
        assert untouched_ai.report["ai_review_status"] == "running"
    expired_ai = await service.FrontendReviews("acme", "repo", 9).get(third.id)
    assert (
        expired_ai is not None and expired_ai.status == "complete" and expired_ai.report is not None
    )
    assert expired_ai.report["ai_review_status"] == "failed"
    assert expired_ai.report["assessment_error"]
    with pytest.raises(FrontendReviewConflict):
        await service.FrontendReviews("acme", "repo", 7).finish(first.id, status="failed")


def _run_as(monkeypatch: pytest.MonkeyPatch, thread_id: str, head_sha: str | None) -> None:
    config = RunConfig.parse(
        {
            "thread_id": thread_id,
            "repo": {"owner": "acme", "name": "repo"},
            "pr_number": 7,
            "head_sha": head_sha,
        }
    )
    monkeypatch.setattr(RunConfig, "from_runtime", lambda: config)


@pytest.mark.usefixtures("registry_db")
async def test_only_the_assigned_thread_can_read_or_publish_an_assessment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    review = await service.FrontendReviews("acme", "repo", 7).create(
        StartFrontendReview(
            base_url="https://base.vercel.app", head_url="https://head.vercel.app", auth_mode="none"
        )
    )
    captured = await service.FrontendReviews("acme", "repo", 7).finish(
        review.id, status="complete", report=_report(), assets=_assets()
    )
    _run_as(monkeypatch, "assessing-thread", review.head_sha)
    with pytest.raises(ValueError, match="does not own"):
        await frontend_review_tools.read_frontend_review(str(review.id))
    with pytest.raises(PermissionError, match="does not own"):
        await frontend_review_tools.submit_frontend_review(str(review.id), "Owned assessment")
    await service.FrontendReviews(
        captured.owner, captured.repo, captured.pr_number
    ).set_assessment_state(captured, "running", thread_id="assessing-thread")
    _run_as(monkeypatch, "other-pr-run", review.head_sha)
    with pytest.raises(ValueError, match="does not own"):
        await frontend_review_tools.read_frontend_review(str(review.id))
    with pytest.raises(PermissionError, match="does not own"):
        await frontend_review_tools.submit_frontend_review(str(review.id), "Owned assessment")
    with pytest.raises(FrontendReviewConflict, match="assigned to another thread"):
        await service.FrontendReviews(
            captured.owner, captured.repo, captured.pr_number
        ).set_assessment_state(captured, "running", thread_id="other-pr-run")
    _run_as(monkeypatch, "assessing-thread", review.head_sha)
    evidence = await frontend_review_tools.read_frontend_review(str(review.id))
    assert [block["type"] for block in evidence].count("image") == 2
    result = await frontend_review_tools.submit_frontend_review(str(review.id), "Owned assessment")
    assert result["ai_review_status"] == "complete"
    restored = await service.FrontendReviews("acme", "repo", 7).get(review.id)
    assert restored is not None and restored.report is not None
    assert restored.report["summary"] == "Owned assessment"
    assert restored.report["assessment_thread_id"] == "assessing-thread"
