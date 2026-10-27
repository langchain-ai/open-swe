import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review import design, standard
from openswe.human_review.design import DesignReview
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.picking import Area, Coverage, ReviewerAvailability
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from openswe.slack.client import GitHubPrRef
from openswe.users import User, UserIdentity


def _user(login: str) -> User:
    return User(
        identities=[
            UserIdentity(provider="github", external_id=login, login=login),
            UserIdentity(provider="slack", external_id=f"U{login}", login=login),
        ]
    )


@pytest.fixture
def review(monkeypatch: pytest.MonkeyPatch) -> HumanReviewRequest:
    pr = PullRequest(owner="langchain-ai", repo="langchainplus", number=7, author="author")
    request = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="standard",
        slack_channel_id="C1",
        slack_message_ts="1.0",
    )
    request.pull_request = pr
    lock = asyncio.Lock()

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        async with lock:
            yield None, request

    monkeypatch.setattr(
        design.RepoSettings,
        "cached",
        AsyncMock(
            return_value=RepoSettings.model_validate(
                {"designReview": {"team": "example/designers", "paths": ["smith-frontend/*"]}}
            )
        ),
    )
    monkeypatch.setattr(HumanReviewRequest, "locked", locked)
    return request


@pytest.mark.parametrize(
    "path,assess",
    [
        ("smith-frontend/src/pages/NewFlow.tsx", True),
        ("smith-frontend/src/components/button.css", True),
        ("smith-frontend/src/pages/NewFlow.test.tsx", False),
        ("smith-frontend/src/__tests__/ManyScreens.tsx", False),
        ("smith-frontend/tests/e2e/navigation.ts", False),
        ("smith-frontend/package.json", False),
        ("openswe/backend.py", False),
    ],
)
async def test_only_production_ui_changes_schedule_semantic_assessment(
    review: HumanReviewRequest, path: str, assess: bool
) -> None:
    engineer = _user("engineer")
    participant = HumanReviewParticipant(user_id=engineer.id, decision="review")
    participant.user = engineer
    review.participants.append(participant)
    with (
        patch.object(ChangedFile, "of_pull", AsyncMock(return_value=[ChangedFile(filename=path)])),
        patch.object(design.queue, "schedule", AsyncMock()) as schedule,
    ):
        await DesignReview(review).start(MagicMock())
    assert bool(schedule.await_count) is assess
    assert review.participants == [participant]


async def test_failed_assessment_dispatch_retries_then_runs_once(
    review: HumanReviewRequest,
) -> None:
    review.thread_id = "engineering-thread"
    review.requested_by = _user("requester")
    with (
        patch.object(
            design,
            "start_dashboard_thread",
            AsyncMock(side_effect=[RuntimeError("unavailable"), "assessment-thread"]),
        ) as notify,
        patch.object(design.queue, "schedule", AsyncMock()),
    ):
        assert await DesignReview(review)._assess() == "retrying"
        assert not DesignReview(review).state.assessment_requested
        assert await DesignReview(review)._assess() == "assessment_requested"
        assert await DesignReview(review)._assess() == "assessed"
    assert notify.await_count == 2
    assert review.thread_id == "engineering-thread"
    assert notify.await_args is not None
    assert notify.await_args.kwargs["unlisted"] is True


async def test_design_delivery_is_idempotent_and_retries_only_missing_notifications(
    review: HumanReviewRequest,
) -> None:
    user = _user("designer")
    review.run_config = {"design_review": {"reason": "New trace navigation flow"}}
    pull = MagicMock(spec=PullRequestClient)
    pull.request_reviewers = AsyncMock()
    pull.comment = AsyncMock()
    with (
        patch.object(
            DesignReview, "_choose", AsyncMock(return_value=(user, timedelta(0)))
        ) as choose,
        patch.object(User, "for_login", AsyncMock(return_value=user)),
        patch.object(
            design, "post_slack_thread_reply_with_ts", AsyncMock(side_effect=["", "1.1"])
        ) as thread_reply,
        patch.object(design, "send_dm", AsyncMock(side_effect=[False, True])) as dm,
        patch.object(design.queue, "schedule", AsyncMock()),
    ):
        assert await DesignReview(review)._assign(pull) == "retrying"
        assert await DesignReview(review)._assign(pull) == "requested"
        assert await DesignReview(review)._assign(pull) == "requested"
    assert choose.await_count == 1
    assert pull.request_reviewers.await_count == 1
    assert pull.comment.await_count == 1
    assert thread_reply.await_count == 2
    assert thread_reply.await_args is not None
    assert thread_reply.await_args.args[:2] == ("C1", "1.0")
    assert dm.await_count == 2
    assert review.participants == []


async def test_rotation_uses_least_recent_available_team_member(review: HumanReviewRequest) -> None:
    people = {
        name: _user(name) for name in ("author", "recent", "older", "ooo", "optedout", "unknown")
    }
    now = datetime.now(UTC)
    pull = MagicMock(spec=PullRequestClient)
    pull.repo = MagicMock()
    pull.repo.can_write = AsyncMock(return_value=True)

    async def user_for_login(_: str, login: str) -> User:
        return people[login]

    async def availability(user: User) -> ReviewerAvailability:
        return ReviewerAvailability(
            None, {"ooo": True, "unknown": None}.get(user.github_login, False)
        )

    async def opted_in(login: str) -> bool:
        return login != "optedout"

    with (
        patch.object(design, "team_members", AsyncMock(return_value=list(people))),
        patch.object(User, "for_login", user_for_login),
        patch.object(design, "latest_review_states", AsyncMock(return_value={})),
        patch.object(design.PROFILES, "allows_auto_human_review", opted_in),
        patch.object(ReviewerAvailability, "for_user", availability),
        patch.object(
            HumanReviewRequest,
            "design_review_history",
            AsyncMock(return_value={"recent": now, "older": now - timedelta(days=1)}),
        ),
    ):
        chosen, delay = await DesignReview(review)._choose(pull)
        assert chosen is people["older"]
        assert delay == timedelta(0)
        with patch.object(
            ReviewerAvailability,
            "for_user",
            AsyncMock(return_value=ReviewerAvailability(ZoneInfo("UTC"))),
        ):
            with patch.object(design, "datetime") as clock:
                clock.now.return_value = datetime(2026, 9, 28, 3, tzinfo=UTC)
                clock.min = datetime.min
                chosen, delay = await DesignReview(review)._choose(pull)
        assert chosen is not None
        assert delay == timedelta(hours=6)


@pytest.mark.parametrize("draft", [True, False])
async def test_design_job_never_pings_draft_or_closed_pr(
    review: HumanReviewRequest, draft: bool
) -> None:
    pull = MagicMock()
    pull.pull = AsyncMock(return_value={"state": "open" if draft else "closed", "draft": draft})
    with (
        patch.object(PullRequestClient, "as_app") as app,
        patch.object(design, "skip_on_preview", return_value=False),
        patch.object(design.queue, "schedule", AsyncMock()),
        patch.object(DesignReview, "_assign", AsyncMock()) as assign,
        patch.object(DesignReview, "_assess", AsyncMock()) as assess,
    ):
        app.return_value.__aenter__.return_value = pull
        assert await DesignReview(review).run() == "not_ready"
    assign.assert_not_awaited()
    assess.assert_not_awaited()


@pytest.mark.parametrize("engineering_role", ["none", "owner", "reviewer"])
async def test_design_approval_only_counts_for_engineering_when_they_also_have_that_role(
    review: HumanReviewRequest, engineering_role: str
) -> None:
    review.run_config = {"design_review": {"github_login": "designer"}}
    coverage = (
        Coverage((Area(("@frontend",), frozenset({"designer"}), ("screen.tsx",)),))
        if engineering_role == "owner"
        else Coverage(())
    )
    if engineering_role == "reviewer":
        user = _user("designer")
        participant = HumanReviewParticipant(user_id=user.id, decision="review")
        participant.user = user
        review.participants.append(participant)
    states = {"designer": "APPROVED", "engineer": "CHANGES_REQUESTED"}
    with patch.object(Coverage, "load", AsyncMock(return_value=coverage)):
        result = await DesignReview(review).engineering_states(states)
    assert (result.get("designer") == "APPROVED") is (engineering_role != "none")
    assert result["engineer"] == "CHANGES_REQUESTED"


@pytest.mark.parametrize("kind", ["posted", "standard"])
async def test_design_request_reuses_posted_review_without_replacing_engineering(
    review: HumanReviewRequest,
    kind: str,
) -> None:
    review.kind = kind
    review.tldr = "Original engineering summary"
    reason = "Redesigned experiment comparison workflow"
    pr = review.pull_request
    with (
        patch.object(HumanReviewRequest, "active_for", AsyncMock(return_value=review)),
        patch.object(standard, "_permalink", AsyncMock(return_value="https://slack.example/card")),
        patch.object(standard, "review_blockers", AsyncMock(return_value=[])),
        patch.object(
            ChangedFile,
            "of_pull",
            AsyncMock(return_value=[ChangedFile(filename="smith-frontend/src/Comparison.tsx")]),
        ),
        patch.object(design.queue, "schedule", AsyncMock()),
        patch.object(ReviewCard, "post_standard", AsyncMock()) as post,
    ):
        result = await standard._request_review(
            GitHubPrRef(owner=pr.owner, repo=pr.repo, number=pr.number, url=pr.url),
            MagicMock(),
            standard.Origin(),
            channel="",
            inline_summary=None,
            design_review_reason=reason,
        )
    assert result.reused and result.success
    assert review.kind == kind
    assert review.tldr == "Original engineering summary"
    assert DesignReview(review).state.reason == reason
    assert review.participants == []
    post.assert_not_awaited()


async def test_design_only_approval_keeps_the_engineering_card_open(
    review: HumanReviewRequest,
) -> None:
    review.run_config = {"design_review": {"github_login": "designer"}}
    review.created_at = datetime.now(UTC) - timedelta(hours=3)
    with (
        patch.object(
            ReviewCard, "_review_states", AsyncMock(return_value={"designer": "APPROVED"})
        ),
        patch.object(ReviewCard, "_files", AsyncMock(return_value=[])),
        patch.object(HumanReviewRequest, "author_mention", AsyncMock(return_value="@author")),
        patch.object(Coverage, "load", AsyncMock(return_value=None)),
    ):
        text, _ = await ReviewCard(review)._render_standard(None)
    assert not text.startswith(":white_check_mark: ")
    assert "approved by @designer" not in text
    assert review.state == "open"


async def test_unconfigured_repository_does_not_start_design_review(
    review: HumanReviewRequest,
) -> None:
    with (
        patch.object(design.RepoSettings, "cached", AsyncMock(return_value=RepoSettings())),
        patch.object(ChangedFile, "of_pull", AsyncMock()) as files,
        patch.object(design.queue, "schedule", AsyncMock()) as schedule,
    ):
        await DesignReview(review).start(MagicMock())
    files.assert_not_awaited()
    schedule.assert_not_awaited()
