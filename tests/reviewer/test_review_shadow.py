"""Shadow reviews: sampled, kept apart from the pull request's own findings, never published."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from openswe.dashboard.options import default_model_pair
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.database import postgres
from openswe.github.pull_requests import PullRequest
from openswe.review.findings import (
    Finding,
    Severity,
    append_finding,
    list_findings,
    new_finding,
)
from openswe.review.publish import ReviewAssessment
from openswe.review.shadow import ReviewShadow
from openswe.run_config import RunConfig
from openswe.tools.publish_review import publish_review


def _f(finding_id: str, *, sha: str = "h1", severity: Severity = "high") -> Finding:
    return new_finding(
        severity=severity,
        category="correctness",
        file="foo.py",
        start_line=10,
        end_line=10,
        description=finding_id,
        sha=sha,
        finding_id=finding_id,
    )


async def _shadow(number: int = 1) -> ReviewShadow:
    pull = await PullRequest(owner="acme", repo="app", number=number).ensure()
    async with postgres.session() as session:
        shadow = ReviewShadow(
            pull_request_id=pull.id,
            reviewer_thread_id=f"shadow-{number}",
            model_id="model",
            reasoning_effort="effort",
        )
        session.add(shadow)
    return shadow


async def _stored(thread_id: str) -> ReviewShadow:
    async with postgres.session() as session:
        shadow = await session.scalar(
            select(ReviewShadow).where(ReviewShadow.reviewer_thread_id == thread_id)
        )
    assert shadow is not None
    return shadow


async def _publish(
    ranking: list[str], assessment: ReviewAssessment | None = None, **configurable: object
) -> dict[str, object]:
    config = {
        "configurable": {
            "thread_id": "shadow-1",
            "repo": {"owner": "acme", "name": "app"},
            "pr_number": 1,
            "review_shadow": True,
            **configurable,
        },
        "metadata": {},
    }
    with (
        patch("openswe.tools.publish_review.get_config", return_value=config),
        patch("openswe.tools.publish_review.get_thread_id_from_runtime", return_value="shadow-1"),
    ):
        return await publish_review(ranking=ranking, assessment=assessment)


@pytest.mark.usefixtures("registry_db")
async def test_a_shadow_keeps_its_findings_apart_and_publishes_only_a_snapshot() -> None:
    await _shadow()
    control_thread = AsyncMock()
    control_thread.threads.get.return_value = {
        "metadata": {"pr": {"owner": "acme", "name": "app", "number": 1}}
    }
    with (
        patch("openswe.review.findings.get_client", return_value=control_thread),
        patch("openswe.tools.publish_review.post_pull_request_review", AsyncMock()) as post,
        patch("openswe.tools.publish_review.resolve_thread_github_token", AsyncMock()) as token,
    ):
        await append_finding("tid", _f("f_control"))
        await append_finding("shadow-1", _f("f_a"))
        await append_finding("shadow-1", _f("f_low", severity="low"))
        first = await _publish(["f_a", "f_low"], head_sha="h1")
        # Left by an earlier run that never published: a re-review of a later head skips it.
        await append_finding("shadow-1", _f("f_stale", sha="h1"))
        await append_finding("shadow-1", _f("f_new", sha="h2"))
        second = await _publish(
            ["f_new"],
            ReviewAssessment(
                head_sha="b" * 40, risk_score=1, decision="would_approve", explanation="Docs only."
            ),
            head_sha="h2",
            re_review=True,
        )
        control = await list_findings("tid")

    assert [finding["id"] for finding in control] == ["f_control"]
    assert (first["dry_run"], first["review_id"], first["surfaced_count"]) == (True, None, 1)
    assert second["surfaced_count"] == 1
    post.assert_not_awaited()
    token.assert_not_awaited()
    shadow = await _stored("shadow-1")
    assert [finding["id"] for finding in shadow.findings] == ["f_a", "f_low", "f_stale", "f_new"]
    assert [
        (p["reviewed_from_sha"], p["head_sha"], p["finding_ids"]) for p in shadow.publications
    ] == [(None, "h1", ["f_a"]), ("h1", "h2", ["f_new"])]
    # As on GitHub, open findings keep an assessment from approving.
    assessment = shadow.publications[1]["assessment"]
    assert assessment is not None and assessment["decision"] == "needs_human_review"
    assert shadow.last_reviewed_sha == "h2"


@pytest.mark.usefixtures("registry_db")
async def test_a_sampled_pull_requests_shadow_follows_its_reviews_from_its_own_commit() -> None:
    model, effort = default_model_pair()
    settings = WorkspaceSettings(
        {
            "review_shadow_percent": 100,
            "review_shadow_model": model,
            "review_shadow_reasoning_effort": effort,
        }
    )
    client = MagicMock()
    client.threads.update = AsyncMock()

    def control(number: int, **configurable: object) -> RunConfig:
        return RunConfig.parse(
            {
                "source": "github_pull_request",
                "repo": {"owner": "acme", "name": "app"},
                "pr_number": number,
                "base_sha": "base",
                **configurable,
            }
        )

    with (
        patch("openswe.review.shadow.cached_workspace_settings", AsyncMock(return_value=settings)),
        patch("openswe.review.shadow.dispatch_client", return_value=client),
        patch("openswe.review.shadow.ensure_titled_thread", AsyncMock()),
        patch("openswe.review.shadow.dispatch_agent_run", AsyncMock()) as dispatch,
    ):
        # A pull request is sampled only when its first review starts.
        await ReviewShadow.follow(
            control(2, head_sha="h2", re_review=True, last_reviewed_sha="h1"),
            kickoff="Re-review",
            pr_title="Other",
        )
        assert dispatch.await_count == 0

        await ReviewShadow.follow(control(1, head_sha="h1"), kickoff="Review", pr_title="Title")
        await ReviewShadow.follow(
            control(1, head_sha="h2", re_review=True, last_reviewed_sha="h1"),
            kickoff="Re-review",
            pr_title="Title",
        )
        async with postgres.session() as session:
            shadow = await session.scalar(select(ReviewShadow))
            assert shadow is not None
            shadow.last_reviewed_sha = "h0"
        await ReviewShadow.follow(
            control(1, head_sha="h3", re_review=True, last_reviewed_sha="h2"),
            kickoff="Re-review",
            pr_title="Title",
        )

    configurables = [call.args[2] for call in dispatch.await_args_list]
    assert [call.args[0] for call in dispatch.await_args_list] == [shadow.reviewer_thread_id] * 3
    assert all(
        c["review_shadow"] is True
        and (c["reviewer_model_id"], c["reviewer_reasoning_effort"]) == (model, effort)
        for c in configurables
    )
    # Until it has published, the shadow reviews the whole pull request; then from its own commit.
    assert [(c.get("re_review"), c.get("last_reviewed_sha")) for c in configurables] == [
        (None, None),
        (None, None),
        (True, "h0"),
    ]
