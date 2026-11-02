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
from openswe.github.repo_files import RepoSettings, SupplementaryReviewPolicy
from openswe.human_review import deadlines, standard, supplementary
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.picking import Area, Coverage, ReviewerAvailability
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from openswe.human_review.supplementary import (
    SupplementaryReview,
    SupplementaryReviews,
    SupplementaryState,
)
from openswe.slack.channels import SlackChannel
from openswe.slack.client import GitHubPrRef
from openswe.users import User, UserIdentity

POLICY = SupplementaryReviewPolicy(
    id="design",
    label="Design review",
    team="example/designers",
    paths=["smith-frontend/src/*"],
    exclude=["*.test.*", "*/__tests__/*"],
    criteria="Substantial user-visible UX changes.",
)
SETTINGS = RepoSettings(supplementaryReviews=[POLICY])


def _review(request: HumanReviewRequest) -> SupplementaryReview:
    return SupplementaryReview(request, POLICY)


def _state(request: HumanReviewRequest) -> SupplementaryState:
    return _review(request).state


def _stored(**state: object) -> dict[str, object]:
    return {"supplementary_reviews": {"design": state}}


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

    monkeypatch.setattr(supplementary.RepoSettings, "cached", AsyncMock(return_value=SETTINGS))
    monkeypatch.setattr(HumanReviewRequest, "locked", locked)

    @asynccontextmanager
    async def rotation(
        self: HumanReviewRequest, policy_id: str
    ) -> AsyncIterator[tuple[HumanReviewRequest, dict[str, datetime]]]:
        async with lock:
            yield request, {}

    monkeypatch.setattr(HumanReviewRequest, "supplementary_rotation", rotation)
    channel = SlackChannel(
        id="C1",
        payload={
            "is_channel": True,
            "is_private": False,
            "is_im": False,
            "is_mpim": False,
            "is_ext_shared": False,
            "is_pending_ext_shared": False,
        },
    )
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=channel))
    return request


@pytest.mark.parametrize(
    "path,assess",
    [
        ("smith-frontend/src/pages/NewFlow.tsx", True),
        ("smith-frontend/src/pages/NewFlow.test.tsx", False),
        ("smith-frontend/src/__tests__/ManyScreens.tsx", False),
        ("smith-frontend/package.json", False),
    ],
)
async def test_only_configured_paths_schedule_an_assessment(
    review: HumanReviewRequest, path: str, assess: bool
) -> None:
    engineer = _user("engineer")
    participant = HumanReviewParticipant(user_id=engineer.id, decision="review")
    participant.user = engineer
    review.participants.append(participant)
    with (
        patch.object(ChangedFile, "of_pull", AsyncMock(return_value=[ChangedFile(filename=path)])),
        patch.object(deadlines.queue, "schedule", AsyncMock()) as schedule,
    ):
        await SupplementaryReviews(review).start(MagicMock())
    assert bool(schedule.await_count) is assess
    assert review.participants == [participant]


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_assessment_dispatch_retries_then_runs_once(
    review: HumanReviewRequest,
    cancelled: bool,
) -> None:
    review.thread_id = "engineering-thread"
    review.requested_by = _user("requester")
    with (
        patch.object(
            supplementary,
            "start_dashboard_thread",
            AsyncMock(
                side_effect=[
                    asyncio.CancelledError() if cancelled else RuntimeError("unavailable"),
                    "assessment-thread",
                ]
            ),
        ) as notify,
        patch.object(deadlines.queue, "schedule", AsyncMock()),
    ):
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await _review(review)._assess()
        else:
            assert await _review(review)._assess() == "retrying"
        assert not _state(review).assessment_requested
        assert await _review(review)._assess() == "assessment_requested"
        assert await _review(review)._assess() == "assessed"
    assert notify.await_count == 2
    assert review.thread_id == "engineering-thread"
    assert notify.await_args is not None
    assert notify.await_args.kwargs["unlisted"] is True


async def test_stranded_assessment_claim_retries_after_expiring(review: HumanReviewRequest) -> None:
    review.requested_by = _user("requester")
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)
    review.run_config = _stored(assessment_started_at=now.isoformat())
    with (
        patch.object(supplementary, "datetime") as clock,
        patch.object(
            supplementary, "start_dashboard_thread", AsyncMock(return_value="assessment")
        ) as dispatch,
        patch.object(SupplementaryReview, "_schedule", AsyncMock()) as retry,
    ):
        clock.now.return_value = now + timedelta(minutes=30)
        assert await _review(review)._assess() == "assessing"
        dispatch.assert_not_awaited()
        retry.assert_awaited_once_with(timedelta(minutes=30))
        clock.now.return_value = now + timedelta(hours=2)
        assert await _review(review)._assess() == "assessment_requested"
    assert _state(review).assessment_requested


@pytest.mark.parametrize("dm_succeeds", [False, True])
async def test_delivery_is_idempotent_and_retries_only_missing_notifications(
    review: HumanReviewRequest,
    dm_succeeds: bool,
) -> None:
    user = _user("designer")
    review.run_config = _stored(reason="New trace navigation flow")
    pull = MagicMock(spec=PullRequestClient)
    pull.request_reviewers = AsyncMock()
    pull.comment = AsyncMock()
    with (
        patch.object(
            SupplementaryReview,
            "_candidates",
            AsyncMock(return_value=[(user, ReviewerAvailability(None))]),
        ) as choose,
        patch.object(User, "for_login", AsyncMock(return_value=user)),
        patch.object(
            supplementary,
            "post_slack_thread_reply_with_ts",
            AsyncMock(side_effect=[supplementary.SlackRequestError("not_in_channel"), "1.1"]),
        ) as thread_reply,
        patch.object(supplementary, "send_dm", AsyncMock(return_value=dm_succeeds)) as dm,
        patch.object(deadlines.queue, "schedule", AsyncMock()),
    ):
        assert await _review(review)._assign(pull) == ("requested" if dm_succeeds else "retrying")
        assert await _review(review)._assign(pull) == "requested"
        assert await _review(review)._assign(pull) == "requested"
    assert choose.await_count == 1
    assert pull.request_reviewers.await_count == 1
    pull.comment.assert_not_awaited()
    assert thread_reply.await_count == (1 if dm_succeeds else 2)
    assert thread_reply.await_args is not None
    assert thread_reply.await_args.args[:2] == ("C1", "1.0")
    assert dm.await_count == 1
    assert review.participants == []


@pytest.mark.parametrize("channel_known", [False, True])
async def test_unreadable_review_channel_uses_one_dm(
    review: HumanReviewRequest, channel_known: bool
) -> None:
    user = _user("designer")
    review.run_config = _stored(reason="New trace navigation flow", github_login="designer")
    pull = MagicMock(spec=PullRequestClient)
    pull.request_reviewers = AsyncMock()
    with (
        patch.object(User, "for_login", AsyncMock(return_value=user)),
        patch.object(
            SlackChannel,
            "load",
            AsyncMock(
                return_value=SlackChannel(id="C1", payload={"is_private": True})
                if channel_known
                else None
            ),
        ),
        patch.object(
            supplementary, "post_slack_thread_reply_with_ts", AsyncMock(return_value="1.1")
        ) as post,
        patch.object(supplementary, "send_dm", AsyncMock(return_value=True)) as dm,
    ):
        assert await _review(review)._assign(pull) == "requested"
        assert await _review(review)._assign(pull) == "requested"
    post.assert_not_awaited()
    dm.assert_awaited_once()


@pytest.mark.parametrize("text_fails", [False, True])
async def test_rejected_screenshot_retries_as_text_without_a_second_ping(
    review: HumanReviewRequest,
    text_fails: bool,
) -> None:
    user = _user("designer")
    review.run_config = _stored(reason="High impact: new navigation", github_login="designer")
    review.pull_request.body = """<!-- ![hidden](https://example.com/hidden.png) -->
```md
![example](https://example.com/example.png)
```
<img src="http://example.com/insecure.png">
<img src="https://example.com/before.png?raw=1&amp;size=640" alt="Before" width="640">

![Before again](https://example.com/before.png?raw=1&size=640)
![After](https://example.com/after.png "Updated navigation")
![Extra](https://example.com/extra.png)
"""
    pull = MagicMock(spec=PullRequestClient)
    pull.request_reviewers = AsyncMock()
    with (
        patch.object(User, "for_login", AsyncMock(return_value=user)),
        patch.object(
            supplementary,
            "post_slack_thread_reply_with_ts",
            AsyncMock(
                side_effect=[
                    supplementary.SlackRequestError("invalid_blocks"),
                    supplementary.SlackRequestError("channel_not_found") if text_fails else "1.1",
                ]
            ),
        ) as post,
        patch.object(supplementary, "send_dm", AsyncMock(return_value=True)) as dm,
    ):
        assert await _review(review)._assign(pull) == "requested"
        assert await _review(review)._assign(pull) == "requested"
    assert post.await_count == 2
    assert post.await_args_list[0].kwargs["blocks"][1:] == [
        {
            "type": "image",
            "image_url": "https://example.com/before.png?raw=1&size=640",
            "alt_text": "Before",
        },
        {"type": "image", "image_url": "https://example.com/after.png", "alt_text": "After"},
    ]
    assert "blocks" not in post.await_args_list[1].kwargs
    assert dm.await_count == int(text_fails)


@pytest.mark.parametrize(
    "ineligible", ["", "author", "reviewed", "optedout", "missing", "no-write", "no-slack"]
)
async def test_mapped_owner_waits_only_for_temporary_unavailability(
    review: HumanReviewRequest,
    ineligible: str,
) -> None:
    policy = SupplementaryReviewPolicy.model_validate(
        {
            **POLICY.model_dump(),
            "id": "experience",
            "areas": {"checkout": {"label": "Checkout and payments", "githubLogin": "area-owner"}},
        }
    )
    review.run_config = {
        "supplementary_reviews": {
            policy.id: {"reason": "Medium impact: new checkout flow", "area": "checkout"}
        }
    }
    owner, other = _user("area-owner"), _user("other")
    pull = MagicMock(spec=PullRequestClient)
    pull.repo = MagicMock()
    pull.repo.can_write = AsyncMock(return_value=True)
    users = {user.github_login: user for user in (other, owner)}
    if ineligible == "missing":
        del users[owner.github_login]
    if ineligible == "author":
        review.pull_request.author = owner.github_login
    if ineligible == "no-slack":
        owner.identities = [
            identity for identity in owner.identities if identity.provider != "slack"
        ]
    reviews = {owner.github_login: "APPROVED"} if ineligible == "reviewed" else {}

    async def can_write(login: str) -> bool:
        return login != owner.github_login or ineligible != "no-write"

    async def opted_in(login: str) -> bool:
        return login != owner.github_login or ineligible != "optedout"

    pull.repo.can_write = can_write

    async def find_user(provider: str, login: str) -> User:
        return users[login]

    with (
        patch.object(supplementary, "team_members", AsyncMock(return_value=list(users))),
        patch.object(User, "for_login", find_user),
        patch.object(supplementary, "latest_review_states", AsyncMock(return_value=reviews)),
        patch.object(supplementary.PROFILES, "allows_auto_human_review", opted_in),
        patch.object(
            ReviewerAvailability,
            "for_user",
            AsyncMock(return_value=ReviewerAvailability(None, False)),
        ),
    ):
        candidates = await SupplementaryReview(review, policy)._candidates(pull)
        assert [user.github_login for user, _ in candidates] == [
            other.github_login if ineligible else owner.github_login
        ]
        with patch.object(
            ReviewerAvailability,
            "for_user",
            AsyncMock(return_value=ReviewerAvailability(None, True)),
        ):
            assert await SupplementaryReview(review, policy)._candidates(pull) == []


@pytest.mark.parametrize("hour", [3, 12])
async def test_concurrent_rotation_reserves_different_available_team_members(
    registry_db: None,
    hour: int,
) -> None:
    people = {
        name: _user(name) for name in ("author", "recent", "older", "ooo", "optedout", "unknown")
    }
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)
    requests = []
    for number, login in enumerate(("recent", "older", "", ""), start=1):
        pr = await PullRequest(owner="example", repo="app", number=number, author="author").save()
        request = await HumanReviewRequest(
            pull_request_id=pr.id,
            head_sha="abc",
            kind="standard",
            slack_channel_id="C1",
            slack_message_ts="1.0",
            run_config=_stored(
                reason="New navigation",
                github_login=login,
                assigned_at=(
                    now - (timedelta(hours=1) if login == "recent" else timedelta(days=1))
                ).isoformat()
                if login
                else None,
            ),
        ).save()
        if not login:
            requests.append(request)
    pull = MagicMock(spec=PullRequestClient)
    pull.repo = MagicMock()
    pull.repo.can_write = AsyncMock(return_value=True)
    pull.request_reviewers = AsyncMock()
    pull.comment = AsyncMock()

    async def user_for_login(_: str, login: str) -> User:
        return people[login]

    async def availability(user: User) -> ReviewerAvailability:
        return ReviewerAvailability(
            ZoneInfo("UTC"), {"ooo": True, "unknown": None}.get(user.github_login, False)
        )

    async def opted_in(login: str) -> bool:
        return login != "optedout"

    with (
        patch.object(supplementary, "team_members", AsyncMock(return_value=list(people))),
        patch.object(User, "for_login", user_for_login),
        patch.object(supplementary, "latest_review_states", AsyncMock(return_value={})),
        patch.object(supplementary.PROFILES, "allows_auto_human_review", opted_in),
        patch.object(ReviewerAvailability, "for_user", availability),
        patch.object(
            supplementary, "post_slack_thread_reply_with_ts", AsyncMock(return_value="1.1")
        ),
        patch.object(supplementary, "send_dm", AsyncMock(return_value=True)),
        patch.object(deadlines.queue, "schedule", AsyncMock()) as schedule,
        patch.object(supplementary, "datetime") as clock,
    ):
        clock.now.return_value = now.replace(hour=hour)
        clock.min = datetime.min
        results = await asyncio.wait_for(
            asyncio.gather(*(_review(request)._assign(pull) for request in requests)),
            timeout=10,
        )
    stored = [await HumanReviewRequest.get(request.id) for request in requests]
    assert all(request is not None for request in stored)
    if hour == 12:
        assert results == ["requested", "requested"]
        assert {_state(request).github_login for request in stored if request is not None} == {
            "older",
            "recent",
        }
    else:
        assert results == ["waiting", "waiting"]
        assert all(not _state(request).github_login for request in stored if request is not None)
        assert schedule.await_count == len(requests)
        assert all(
            timedelta(hours=5, minutes=59)
            < call.kwargs["due_at"] - datetime.now(UTC)
            < timedelta(hours=6, minutes=1)
            for call in schedule.await_args_list
        )


@pytest.mark.parametrize("draft", [True, False])
async def test_job_never_pings_draft_or_closed_pr(review: HumanReviewRequest, draft: bool) -> None:
    pull = MagicMock()
    pull.pull = AsyncMock(return_value={"state": "open" if draft else "closed", "draft": draft})
    with (
        patch.object(PullRequestClient, "as_app") as app,
        patch.object(supplementary, "skip_on_preview", return_value=False),
        patch.object(deadlines.queue, "schedule", AsyncMock()),
        patch.object(SupplementaryReview, "_assign", AsyncMock()) as assign,
        patch.object(SupplementaryReview, "_assess", AsyncMock()) as assess,
    ):
        app.return_value.__aenter__.return_value = pull
        assert await _review(review).run() == "not_ready"
    assign.assert_not_awaited()
    assess.assert_not_awaited()


@pytest.mark.parametrize("engineering_role", ["none", "owner", "reviewer"])
async def test_supplementary_approval_only_counts_for_engineering_with_that_role(
    review: HumanReviewRequest, engineering_role: str
) -> None:
    review.run_config = _stored(github_login="designer")
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
        result = await SupplementaryReviews(review).engineering_states(states)
    assert (result.get("designer") == "APPROVED") is (engineering_role != "none")
    assert result["engineer"] == "CHANGES_REQUESTED"


@pytest.mark.parametrize("kind", ["posted", "standard"])
@pytest.mark.parametrize("schedule_fails", [False, True])
async def test_request_reuses_open_review_without_replacing_engineering(
    review: HumanReviewRequest,
    kind: str,
    schedule_fails: bool,
) -> None:
    review.kind = kind
    review.tldr = "Original engineering summary"
    reason = "Redesigned experiment comparison workflow"
    pr = review.pull_request
    settings = RepoSettings.model_validate(
        {
            "supplementaryReviews": [
                {
                    **POLICY.model_dump(),
                    "areas": {
                        "comparison": {"label": "Comparison flows", "githubLogin": "area-owner"}
                    },
                }
            ]
        }
    )
    with (
        patch.object(supplementary.RepoSettings, "cached", AsyncMock(return_value=settings)),
        patch.object(HumanReviewRequest, "active_for", AsyncMock(return_value=review)),
        patch.object(standard, "_permalink", AsyncMock(return_value="https://slack.example/card")),
        patch.object(standard, "review_blockers", AsyncMock(return_value=[])),
        patch.object(
            ChangedFile,
            "of_pull",
            AsyncMock(return_value=[ChangedFile(filename="smith-frontend/src/Comparison.tsx")]),
        ),
        patch.object(
            deadlines.queue,
            "schedule",
            AsyncMock(side_effect=RuntimeError("queue unavailable") if schedule_fails else None),
        ),
        patch.object(ReviewCard, "post_standard", AsyncMock()) as post,
    ):
        result = await standard._request_review(
            GitHubPrRef(owner=pr.owner, repo=pr.repo, number=pr.number, url=pr.url),
            MagicMock(),
            standard.Origin(),
            channel="",
            inline_summary=None,
            supplementary_review="design",
            supplementary_review_reason=reason,
            supplementary_review_area="comparison",
        )
    assert result.reused and result.success
    assert review.kind == kind
    assert review.tldr == "Original engineering summary"
    assert _state(review).reason == reason
    assert _state(review).area == "comparison"
    assert review.participants == []
    post.assert_not_awaited()


async def test_supplementary_only_approval_keeps_the_engineering_card_open(
    review: HumanReviewRequest,
) -> None:
    review.run_config = _stored(github_login="designer")
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


async def test_unconfigured_repository_does_not_start_supplementary_reviews(
    review: HumanReviewRequest,
) -> None:
    with (
        patch.object(supplementary.RepoSettings, "cached", AsyncMock(return_value=RepoSettings())),
        patch.object(ChangedFile, "of_pull", AsyncMock()) as files,
        patch.object(deadlines.queue, "schedule", AsyncMock()) as schedule,
    ):
        await SupplementaryReviews(review).start(MagicMock())
    files.assert_not_awaited()
    schedule.assert_not_awaited()


@pytest.mark.parametrize(
    ("policy_id", "path", "mapped", "area", "refusal"),
    [
        ("design", "smith-frontend/src/Flow.tsx", False, "", ""),
        ("security", "smith-frontend/src/Flow.tsx", False, "", "configures no `security`"),
        ("design", "smith-frontend/src/Flow.test.tsx", False, "", "match the Design review paths"),
        ("design", "smith-frontend/src/Flow.tsx", True, "checkout", ""),
        ("design", "smith-frontend/src/Flow.tsx", True, "unmapped", ""),
        ("design", "smith-frontend/src/Flow.tsx", True, "", "Choose a configured area"),
        (
            "design",
            "smith-frontend/src/Flow.tsx",
            True,
            "other",
            "Unknown supplementary review area",
        ),
    ],
)
async def test_agent_request_is_refused_when_its_policy_cannot_apply(
    review: HumanReviewRequest, policy_id: str, path: str, mapped: bool, area: str, refusal: str
) -> None:
    settings = RepoSettings.model_validate(
        {
            "supplementaryReviews": [
                {
                    **POLICY.model_dump(),
                    "areas": (
                        {"checkout": {"label": "Checkout", "githubLogin": "area-owner"}}
                        if mapped
                        else {}
                    ),
                }
            ]
        }
    )
    with (
        patch.object(supplementary.RepoSettings, "cached", AsyncMock(return_value=settings)),
        patch.object(ChangedFile, "of_pull", AsyncMock(return_value=[ChangedFile(filename=path)])),
        patch.object(deadlines.queue, "schedule", AsyncMock()),
    ):
        if refusal:
            with pytest.raises(supplementary.SupplementaryReviewRefused, match=refusal):
                await SupplementaryReviews(review).ask(MagicMock(), policy_id, "New flow", area)
        else:
            await SupplementaryReviews(review).ask(MagicMock(), policy_id, "New flow", area)
    assert _state(review).reason == ("" if refusal else "New flow")
    assert _state(review).area == ("" if refusal else area)


@pytest.mark.parametrize(
    "invalid_areas",
    [
        {"checkout": {"label": "Checkout"}},
        {"unmapped": {"label": "Checkout", "githubLogin": "owner"}},
        [],
    ],
)
def test_invalid_policies_are_dropped_without_discarding_other_settings(
    invalid_areas: object,
) -> None:
    settings = RepoSettings.model_validate_json(
        """{
            "reviewChannel": "#eng-reviews",
            "reviewAllowedAuthors": ["langsmith-engine[bot]"],
            "supplementaryReviews": [
                {"id": "Design", "label": "Design review", "team": "a/b", "paths": ["*"],
                 "criteria": "x"},
                {"id": "design", "label": "Design review", "team": "a/b", "paths": ["*"],
                 "criteria": "first"},
                {"id": "design", "label": "Design review", "team": "a/b", "paths": ["*"],
                 "criteria": "second"},
                {"id": "security", "label": "Security review", "team": "a/c", "paths": ["*"]}
            ]
        }"""
    )
    assert settings.review_channel == "#eng-reviews"
    assert settings.review_allowed_authors == ["langsmith-engine[bot]"]
    assert [(p.id, p.criteria) for p in settings.supplementary_reviews] == [("design", "first")]
    configured = RepoSettings.model_validate(
        {
            **settings.model_dump(by_alias=True),
            "supplementaryReviews": [
                {**POLICY.model_dump(), "areas": invalid_areas},
                {**POLICY.model_dump(), "id": "security"},
            ],
        }
    )
    assert configured.review_channel == settings.review_channel
    assert configured.review_allowed_authors == settings.review_allowed_authors
    assert configured.supplementary_reviews == [
        POLICY,
        POLICY.model_copy(update={"id": "security"}),
    ]
