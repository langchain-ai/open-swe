import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from PIL import Image
from runtime_api.frontend_review import (
    CaptureFrontendReview,
    FrontendAssessmentArguments,
    FrontendReviewArguments,
    ReadFrontendReview,
    ReadFrontendReviewArguments,
    SubmitFrontendReview,
)
from sqlalchemy import update

from openswe.agent_api.server import RefusedCallError
from openswe.agent_api.tokens import RemoteRun
from openswe.dashboard.oauth import require_session
from openswe.database import postgres
from openswe.frontend_review import agent_api, launch, routes, service
from openswe.frontend_review.models import FrontendReviewRun
from openswe.frontend_review.schemas import (
    DifferenceMetrics,
    FrontendReport,
    FrontendScenario,
    ReviewAnnotation,
    StartFrontendReview,
)
from openswe.frontend_review.service import EvidenceAsset, FrontendReviewConflict
from openswe.github.http import GitHubClient
from openswe.threads.summary import assert_thread_readable


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
    run = await service.create_review(
        "acme",
        "repo",
        7,
        StartFrontendReview(
            base_url="https://base.vercel.app", head_url="https://head.vercel.app", auth_mode="none"
        ),
    )
    assert (run.base_sha, run.head_sha) == ("a" * 40, "b" * 40)
    assert run.status == "running"
    await service.finish_review(
        run.id,
        owner="acme",
        repo="repo",
        pr_number=7,
        status="complete",
        report=_report(),
        assets=_assets(),
    )
    restored = await service.get_review("acme", "repo", 7, run.id)
    assert restored is not None and restored.status == "complete"
    running = await service.set_assessment_state(restored, "running", thread_id="review-thread")
    assert running.report is not None and running.report["ai_review_status"] == "running"
    failed = await service.set_assessment_state(
        restored, "failed", thread_id="review-thread", error="Model deployment unavailable"
    )
    assert failed.status == "complete" and failed.report is not None
    assert failed.report["assessment_error"] == "Model deployment unavailable"
    view = service.review_view(restored)
    assert view.report is not None
    assert view.report.scenarios[0].before_url == (
        f"/dashboard/api/reviews/acme/repo/7/frontend-reviews/{run.id}/media/before.png"
    )
    assert await service.media_for_review("acme", "repo", 8, run.id, "before.png") is None
    assert await service.media_for_review("other", "repo", 7, run.id, "before.png") is None
    assert await service.media_for_review("acme", "re_o", 7, run.id, "before.png") is None
    media = await service.media_for_review("ACME", "REPO", 7, run.id, "before.png")
    assert media is not None and media.content == _assets()[0].content
    assert restored.report is not None
    with pytest.raises(LookupError):
        await service.assess_review(
            "acme",
            "repo",
            8,
            run.id,
            expected_head_sha=run.head_sha,
            assessment_thread_id="review-thread",
            summary="Incorrect PR",
            annotations_by_scenario={},
        )
    with pytest.raises(FrontendReviewConflict, match="head commit"):
        await service.assess_review(
            "acme",
            "repo",
            7,
            run.id,
            expected_head_sha="c" * 40,
            assessment_thread_id="review-thread",
            summary="Outdated review",
            annotations_by_scenario={},
        )
    with pytest.raises(ValueError, match="captured scenarios"):
        await service.assess_review(
            "acme",
            "repo",
            7,
            run.id,
            expected_head_sha=run.head_sha,
            assessment_thread_id="review-thread",
            summary="Invented evidence",
            annotations_by_scenario={"missing": []},
        )
    with pytest.raises(ValueError, match="inside the captured screenshot"):
        await service.assess_review(
            "acme",
            "repo",
            7,
            run.id,
            expected_head_sha=run.head_sha,
            assessment_thread_id="review-thread",
            summary="Out of bounds",
            annotations_by_scenario={
                "page-1": [
                    ReviewAnnotation(
                        x=0, y=0, width=3, height=1, title="Outside image", body="Invalid region"
                    )
                ]
            },
        )
    annotated = await service.assess_review(
        "acme",
        "repo",
        7,
        run.id,
        expected_head_sha=run.head_sha,
        assessment_thread_id="review-thread",
        summary="The label clips in the updated screenshot.",
        annotations_by_scenario={
            "page-1": [
                ReviewAnnotation(
                    x=0,
                    y=0,
                    width=1,
                    height=1,
                    title="Clipped label",
                    body="The label is truncated.",
                )
            ]
        },
    )
    assert annotated.report is not None and annotated.report["ai_review_status"] == "complete"
    assert annotated.report["assessment_error"] is None
    assert annotated.report["assessment_thread_id"] == "review-thread"
    preserved = await service.set_assessment_state(annotated, "failed", error="Late dispatch error")
    assert preserved.report == annotated.report
    scenario = service.review_view(annotated).report
    assert scenario is not None and scenario.scenarios[0].metrics == _report().scenarios[0].metrics
    assert scenario.scenarios[0].before_url == view.report.scenarios[0].before_url
    with pytest.raises(FrontendReviewConflict, match="cannot be overwritten"):
        await service.assess_review(
            "acme",
            "repo",
            7,
            run.id,
            expected_head_sha=run.head_sha,
            assessment_thread_id="review-thread",
            summary="Overwrite",
            annotations_by_scenario={},
        )
    with pytest.raises(FrontendReviewConflict):
        await service.finish_review(run.id, owner="acme", repo="repo", pr_number=7, status="failed")


@pytest.mark.usefixtures("registry_db")
async def test_dummy_auth_is_terminal_and_stale_agent_revisions_never_create_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    body = StartFrontendReview(
        base_url="https://base.vercel.app", head_url="https://head.vercel.app"
    )
    with pytest.raises(ValueError, match="changed after"):
        await service.create_review("acme", "repo", 7, body, expected_head_sha="c" * 40)
    assert await service.list_reviews("acme", "repo", 7) == []
    run = await service.create_review("acme", "repo", 7, body)
    assert run.status == "blocked" and run.report is None
    assert run.error and "No login or capture was attempted" in run.error


@pytest.mark.usefixtures("registry_db")
async def test_only_the_assigned_thread_can_read_or_publish_an_assessment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    review = await service.create_review(
        "acme",
        "repo",
        7,
        StartFrontendReview(
            base_url="https://base.vercel.app", head_url="https://head.vercel.app", auth_mode="none"
        ),
    )
    captured = await service.finish_review(
        review.id,
        owner="acme",
        repo="repo",
        pr_number=7,
        status="complete",
        report=_report(),
        assets=_assets(),
    )
    read = ReadFrontendReview(request=ReadFrontendReviewArguments(review_id=review.id))
    submit = SubmitFrontendReview(
        request=FrontendAssessmentArguments(review_id=review.id, summary="Owned assessment")
    )
    config = {
        "repo": {"owner": "acme", "name": "repo"},
        "pr_number": 7,
        "head_sha": review.head_sha,
    }
    owner = RemoteRun(
        thread_id="dashboard-assessment", assistant_id="reviewer", configurable=config
    )
    with pytest.raises(RefusedCallError, match="does not own"):
        await agent_api.inspect(owner, read)
    with pytest.raises(RefusedCallError, match="does not own"):
        await agent_api.submit(owner, submit)
    await service.set_assessment_state(captured, "running", thread_id=owner.thread_id)
    for dedicated_id in (None, str(review.id)):
        intruder = RemoteRun(
            thread_id="other-pr-run",
            assistant_id="reviewer",
            configurable={**config, "frontend_review_id": dedicated_id},
        )
        with pytest.raises(RefusedCallError, match="does not own"):
            await agent_api.inspect(intruder, read)
        with pytest.raises(RefusedCallError, match="does not own"):
            await agent_api.submit(intruder, submit)
    with pytest.raises(FrontendReviewConflict, match="assigned to another thread"):
        await service.set_assessment_state(captured, "running", thread_id="other-pr-run")
    evidence = await agent_api.inspect(owner, read)
    assert len(evidence.screenshots) == 2
    result = await agent_api.submit(owner, submit)
    assert result.ai_review_status == "complete"
    restored = await service.get_review("acme", "repo", 7, review.id)
    assert restored is not None and restored.report is not None
    assert restored.report["summary"] == "Owned assessment"
    assert restored.report["assessment_thread_id"] == owner.thread_id


@pytest.mark.usefixtures("registry_db")
@pytest.mark.parametrize("head_sha", ["b" * 40, None])
async def test_agent_capture_assigns_its_evidence_before_assessment(
    monkeypatch: pytest.MonkeyPatch, head_sha: str | None
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)

    async def capture(review: FrontendReviewRun) -> None:
        await service.finish_review(
            review.id,
            owner=review.owner,
            repo=review.repo,
            pr_number=review.pr_number,
            status="complete",
            report=_report(),
            assets=_assets(),
        )

    monkeypatch.setattr(agent_api, "capture_run", capture)
    token = RemoteRun(
        thread_id="capturing-agent",
        assistant_id="agent",
        configurable={
            "repo": {"owner": "acme", "name": "repo"},
            "pr_number": 7,
            "head_sha": head_sha,
        },
    )
    evidence = await agent_api.capture(
        token,
        CaptureFrontendReview(
            request=FrontendReviewArguments(
                base_url="https://base.vercel.app",
                head_url="https://head.vercel.app",
                auth_mode="none",
            )
        ),
    )
    assert len(evidence.screenshots) == 2
    if head_sha is None:
        assert evidence.ai_review_status == "not_run"
        return
    assert evidence.ai_review_status == "running"
    result = await agent_api.submit(
        token,
        SubmitFrontendReview(
            request=FrontendAssessmentArguments(
                review_id=evidence.review_id, summary="Assessment of the agent's own capture"
            )
        ),
    )
    assert result.ai_review_status == "complete"


@pytest.mark.usefixtures("registry_db")
async def test_interrupted_captures_expire_only_within_the_requested_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    request = StartFrontendReview(
        base_url="https://base.vercel.app", head_url="https://head.vercel.app", auth_mode="none"
    )
    first = await service.create_review("acme", "repo", 7, request)
    second = await service.create_review("acme", "repo", 8, request)
    third = await service.create_review("acme", "repo", 9, request)
    completed = await service.finish_review(
        third.id,
        owner="acme",
        repo="repo",
        pr_number=9,
        status="complete",
        report=_report(),
        assets=_assets(),
    )
    await service.set_assessment_state(completed, "running", thread_id="interrupted-ai-thread")
    async with postgres.session() as session:
        await session.execute(
            update(FrontendReviewRun).values(
                created_at=datetime.now(UTC) - timedelta(minutes=16),
                updated_at=datetime.now(UTC) - timedelta(minutes=16),
            )
        )
    [expired] = await service.list_reviews("acme", "repo", 7)
    assert expired.status == "failed" and expired.error and "interrupted" in expired.error
    async with postgres.session() as session:
        untouched = await session.get(FrontendReviewRun, second.id)
        assert untouched is not None and untouched.status == "running"
        untouched_ai = await session.get(FrontendReviewRun, third.id)
        assert untouched_ai is not None and untouched_ai.report is not None
        assert untouched_ai.report["ai_review_status"] == "running"
    expired_ai = await service.get_review("acme", "repo", 9, third.id)
    assert (
        expired_ai is not None and expired_ai.status == "complete" and expired_ai.report is not None
    )
    assert expired_ai.report["ai_review_status"] == "failed"
    assert expired_ai.report["assessment_error"]
    with pytest.raises(FrontendReviewConflict):
        await service.finish_review(
            first.id, owner="acme", repo="repo", pr_number=7, status="failed"
        )


@pytest.mark.parametrize(
    "method,suffix",
    [("GET", ""), ("POST", ""), ("GET", "/{run}"), ("GET", "/{run}/media/before.png")],
)
async def test_all_frontend_evidence_endpoints_require_repository_access(
    monkeypatch: pytest.MonkeyPatch, method: str, suffix: str
) -> None:
    async def denied(login: str, full_name: str) -> None:
        raise HTTPException(403, "Repository access denied")

    monkeypatch.setattr(routes, "require_repo_access_for_user", denied)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_session] = lambda: {"sub": "octocat"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.request(
            method,
            "/reviews/acme/repo/7/frontend-reviews" + suffix.format(run=uuid4()),
            json={"base_url": "https://base.vercel.app", "head_url": "https://head.vercel.app"}
            if method == "POST"
            else None,
        )
    assert response.status_code == 403


def test_publishing_rejects_external_media_and_mismatched_capture_dimensions() -> None:
    report = _report()
    report.scenarios[0].before_url = "https://external.example/screenshot.png"
    with pytest.raises(ValueError, match="this run's uploaded"):
        service._validate_report(report, _assets(), complete=True)
    report.scenarios[0].before_url = "before.png"
    report.scenarios[0].width = 3
    with pytest.raises(ValueError, match="dimensions"):
        service._validate_report(report, _assets(), complete=True)
    with pytest.raises(ValueError, match="PNG"):
        EvidenceAsset("before.png", "image/png", b"<svg/>").validate()
    report = _report()
    report.ai_review_status = "complete"
    with pytest.raises(ValueError, match="Capture cannot publish an AI assessment"):
        service._validate_report(report, _assets(), complete=True)


@pytest.mark.usefixtures("registry_db")
@pytest.mark.parametrize("dispatch_fails", [False, True])
async def test_assessment_dispatch_keeps_capture_and_binds_the_exact_evidence(
    monkeypatch: pytest.MonkeyPatch, dispatch_fails: bool
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    monkeypatch.setattr(launch, "dispatch_client", lambda: object())
    thread = AsyncMock()
    monkeypatch.setattr(launch, "create_thread", thread)
    dispatch = AsyncMock(
        side_effect=RuntimeError("Runtime unavailable") if dispatch_fails else None,
        return_value={"run_id": "assessment-run"},
    )
    monkeypatch.setattr(launch, "create_durable_run", dispatch)
    dummy = await service.create_review(
        "acme",
        "repo",
        7,
        StartFrontendReview(base_url="https://base.vercel.app", head_url="https://head.vercel.app"),
    )
    await launch.launch_assessment(dummy, "octocat")
    assert not dispatch.called
    run = await service.create_review(
        "acme",
        "repo",
        7,
        StartFrontendReview(
            base_url="https://base.vercel.app", head_url="https://head.vercel.app", auth_mode="none"
        ),
    )
    captured = await service.finish_review(
        run.id,
        owner="acme",
        repo="repo",
        pr_number=7,
        status="complete",
        report=_report(),
        assets=_assets(),
    )
    await launch.launch_assessment(captured, "octocat")
    updated = await service.get_review("acme", "repo", 7, run.id)
    assert updated is not None and updated.status == "complete" and updated.report is not None
    assert updated.report["ai_review_status"] == ("failed" if dispatch_fails else "running")
    assert bool(updated.report["assessment_error"]) == dispatch_fails
    assert updated.report["scenarios"] == captured.report["scenarios"]
    media = await service.media_for_review("acme", "repo", 7, run.id, "before.png")
    assert media is not None and media.content == _assets()[0].content
    config = dispatch.call_args.kwargs["config"]["configurable"]
    assert config["frontend_review_id"] == str(run.id)
    assert config["repo"] == {"owner": "acme", "name": "repo"}
    assert config["pr_number"] == 7
    assert (config["base_sha"], config["head_sha"]) == (run.base_sha, run.head_sha)
    metadata = thread.call_args.kwargs["metadata"]
    assert_thread_readable(metadata, "octocat")
    with pytest.raises(HTTPException, match="thread not found"):
        assert_thread_readable(metadata, "unrelated-user")
