import io
import json
import random
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
    FrontendInteractionStep,
    FrontendJourney,
    FrontendReport,
    FrontendScenario,
    ReviewAnnotation,
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
    run = await service.create_review(
        "acme",
        "repo",
        7,
        StartFrontendReview(
            base_url="https://base.vercel.app",
            head_url="https://head.vercel.app",
            auth_mode="none",
            journeys=[
                FrontendJourney(
                    title="Open dialog",
                    path="/",
                    steps=[
                        FrontendInteractionStep(action="click", selector="#open", checkpoint="Open")
                    ],
                )
            ],
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
    assert restored.journeys == run.journeys and restored.journeys[0]["title"] == "Open dialog"
    running = await service.set_assessment_state(restored, "running", thread_id="review-thread")
    assert running.report is not None and running.report["ai_review_status"] == "running"
    failed = await service.set_assessment_state(
        restored, "failed", thread_id="review-thread", error="Model deployment unavailable"
    )
    assert failed.status == "complete" and failed.report is not None
    assert failed.report["assessment_error"] == "Model deployment unavailable"
    view = service.review_view(restored)
    assert view.report is not None
    # Scenarios name their media; apps read each through the plugin API.
    assert view.report.scenarios[0].before_url == "before.png"
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


def test_image_heavy_routes_and_interaction_states_fit_the_evidence_budget() -> None:
    output = io.BytesIO()
    Image.frombytes("RGB", (1440, 1000), random.Random(0).randbytes(1440 * 1000 * 3)).save(
        output, format="PNG"
    )
    png = output.getvalue()
    report = _report()
    report.scenarios = []
    assets = []
    for index in range(10):
        scenario = _report().scenarios[0]
        scenario.id = f"state-{index}"
        scenario.interaction = index >= 5
        scenario.width, scenario.height = 1440, 1000
        scenario.metrics = DifferenceMetrics(
            changed_pixels=0, total_pixels=1440000, changed_ratio=0
        )
        for field in ("before_url", "after_url", "diff_url", "mask_url"):
            name = f"{index}-{field}.png"
            setattr(scenario, field, name)
            assets.append(EvidenceAsset(name, "image/png", png))
        report.scenarios.append(scenario)
    service._validate_report(report, assets, complete=True)
    with pytest.raises(ValueError, match="per-run limit"):
        service._validate_report(
            report, [*assets, EvidenceAsset("extra.png", "image/png", png)], complete=True
        )
    with pytest.raises(ValueError, match="asset exceeds its size limit"):
        EvidenceAsset("oversized.png", "image/png", bytes(8 * 1024 * 1024 + 1)).validate()


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
    _run_as(monkeypatch, "assessing-thread", review.head_sha)
    with pytest.raises(ValueError, match="does not own"):
        await frontend_review_tools.read_frontend_review(str(review.id))
    with pytest.raises(PermissionError, match="does not own"):
        await frontend_review_tools.submit_frontend_review(str(review.id), "Owned assessment")
    await service.set_assessment_state(captured, "running", thread_id="assessing-thread")
    _run_as(monkeypatch, "other-pr-run", review.head_sha)
    with pytest.raises(ValueError, match="does not own"):
        await frontend_review_tools.read_frontend_review(str(review.id))
    with pytest.raises(PermissionError, match="does not own"):
        await frontend_review_tools.submit_frontend_review(str(review.id), "Owned assessment")
    with pytest.raises(FrontendReviewConflict, match="assigned to another thread"):
        await service.set_assessment_state(captured, "running", thread_id="other-pr-run")
    _run_as(monkeypatch, "assessing-thread", review.head_sha)
    evidence = await frontend_review_tools.read_frontend_review(str(review.id))
    assert [block["type"] for block in evidence].count("image") == 2
    result = await frontend_review_tools.submit_frontend_review(str(review.id), "Owned assessment")
    assert result["ai_review_status"] == "complete"
    restored = await service.get_review("acme", "repo", 7, review.id)
    assert restored is not None and restored.report is not None
    assert restored.report["summary"] == "Owned assessment"
    assert restored.report["assessment_thread_id"] == "assessing-thread"


@pytest.mark.usefixtures("registry_db")
@pytest.mark.parametrize("head_sha", ["b" * 40, None])
async def test_a_runs_own_capture_is_the_one_it_assesses(
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

    monkeypatch.setattr(frontend_review_tools, "capture_run", capture)
    _run_as(monkeypatch, "capturing-agent", head_sha)
    content = await frontend_review_tools.review_frontend(
        "https://base.vercel.app", "https://head.vercel.app", auth_mode="none"
    )
    assert [block["type"] for block in content].count("image") == 2
    report = json.loads(str(content[0]["text"]))
    assert report["report_url"].startswith("/agents/plugins/design-qa/acme/repo/7?run=")
    if head_sha is None:
        # Without the exact head, the capture is evidence but not this run's to assess.
        assert report["ai_review_status"] == "not_run"
        return
    assert report["ai_review_status"] == "running"
    result = await frontend_review_tools.submit_frontend_review(
        report["review_id"], "Assessment of the agent's own capture"
    )
    assert result["ai_review_status"] == "complete"
