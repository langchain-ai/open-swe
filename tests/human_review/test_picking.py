from collections import Counter
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx2
import pytest

from openswe.github.codeowners import CodeOwners
from openswe.github.pull_requests import PullRequest
from openswe.human_review.picking import ReviewerAvailability
from openswe.human_review.requests import HumanReviewRequest
from openswe.users import User, UserIdentity
from tests.conftest import FakeUserRecords

_CODEOWNERS = CodeOwners.parse(
    """
* @default
*.js @js
/docs/ @docs  # nested docs
docs/* @flat
apps/ @apps
/smith-frontend/src/design-system/ @langchain-ai/design-system
/unowned.txt
"""
)


@pytest.mark.parametrize(
    ("path", "owners"),
    [
        ("README.md", ("@default",)),
        ("src/app.js", ("@js",)),
        ("docs/intro.md", ("@flat",)),
        ("docs/guides/setup.md", ("@docs",)),
        ("services/apps/main.py", ("@apps",)),
        ("smith-frontend/src/design-system/Button.tsx", ("@langchain-ai/design-system",)),
        ("unowned.txt", ()),
    ],
)
def test_the_last_matching_codeowners_rule_owns_a_path(path: str, owners: tuple[str, ...]) -> None:
    assert _CODEOWNERS.owners_for(path) == owners


async def test_the_largest_code_owner_area_comes_first_and_an_approval_leaves_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.human_review import picking

    monkeypatch.setattr(picking, "team_members", AsyncMock(return_value=["Grace", "Ada"]))
    coverage = await picking.Coverage.build(
        _CODEOWNERS,
        [
            "smith-frontend/src/design-system/Button.tsx",
            "smith-frontend/src/design-system/Card.tsx",
            "README.md",
            "unowned.txt",
        ],
    )
    assert [area.handles for area in coverage.areas] == [
        ("@langchain-ai/design-system",),
        ("@default",),
    ]
    assert coverage.areas[0].owners == {"grace", "ada"}
    assert coverage.uncovered(["Grace"]) == [coverage.areas[1]]
    assert coverage.uncovered(["grace", "default"]) == []


@pytest.mark.parametrize("status", [403, 429, 500, 404, 200])
async def test_strict_codeowners_fetch_distinguishes_missing_from_unreadable(
    status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.github import http as github_http
    from openswe.github import repo_files

    response = httpx2.Response(
        status, text="", request=httpx2.Request("GET", "https://api.github.com")
    )
    monkeypatch.setattr(github_http, "github_request", AsyncMock(return_value=response))
    repo = github_http.GitHubClient(MagicMock()).repo("lc", "repo")
    if status in {403, 429, 500}:
        with pytest.raises(repo_files.RepoFileUnreadableError):
            await CodeOwners.fetch(repo, "main", strict=True)
    else:
        owners = await CodeOwners.fetch(repo, "main", strict=True)
        assert (owners is None) == (status == 404)


def test_off_shift_on_friday_evening_waits_for_monday_morning() -> None:
    hours = ReviewerAvailability(ZoneInfo("America/New_York"))
    friday_evening = datetime(2026, 10, 2, 23, tzinfo=UTC)
    assert not hours.on_shift(friday_evening)
    assert hours.next_start(friday_evening) == datetime(2026, 10, 5, 13, tzinfo=UTC)


@pytest.mark.parametrize("codeowners", [CodeOwners.parse("* @ada @grace"), None])
@pytest.mark.parametrize(
    ("profile", "classification", "skip_reason"),
    [
        (None, None, "opted_out"),
        ({"display_name": "Ada (oOo)"}, None, "out_of_office"),
        ({"display_name": "Ada", "real_name": "Ada Smith (OOO)"}, None, "out_of_office"),
        (
            {"status_text": "Vacationing", "status_emoji": ":palm_tree:"},
            "out_of_office",
            "out_of_office",
        ),
        ({"status_text": "Out sick"}, "out_of_office", "out_of_office"),
        ({"status_text": "Back from vacation"}, "not_out_of_office", None),
        ({"status_text": "OOO", "status_expiration": 1}, None, None),
        ({"display_name": "Ada OOO", "status_expiration": 1}, None, "out_of_office"),
        ({"status_text": "In a meeting"}, "not_out_of_office", None),
        ({"status_text": "OOO"}, None, None),
        (None, None, None),
        ({}, None, None),
    ],
)
async def test_auto_picks_skip_unavailable_owners_and_recent_authors(
    monkeypatch: pytest.MonkeyPatch,
    user_records: FakeUserRecords,
    caplog: pytest.LogCaptureFixture,
    codeowners: CodeOwners | None,
    profile: dict[str, object] | None,
    classification: str | None,
    skip_reason: str | None,
) -> None:
    from openswe.expedited_review.eligibility import ChangedFile
    from openswe.human_review import picking
    from openswe.slack import availability

    monkeypatch.setattr(availability, "select_jev_choice", AsyncMock(return_value=classification))
    ada = User(
        identities=[
            UserIdentity(provider="github", external_id="1", login="ada"),
            UserIdentity(provider="slack", external_id="U123"),
        ]
    )
    grace = User(identities=[UserIdentity(provider="github", external_id="2", login="grace")])
    if skip_reason == "opted_out":
        user_records.seed("profile", "ada", {"auto_human_review_enabled": False})
    monkeypatch.setattr(
        picking,
        "get_slack_user_info",
        AsyncMock(return_value={"profile": profile} if profile is not None else None),
    )
    pr = PullRequest(owner="lc", repo="repo", number=7, author="author")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    monkeypatch.setattr(
        ChangedFile, "of_pull", AsyncMock(return_value=[ChangedFile(filename="app.py")])
    )
    monkeypatch.setattr(CodeOwners, "fetch", AsyncMock(return_value=codeowners))
    monkeypatch.setattr(picking, "_touched", AsyncMock(return_value=Counter(ada=10, grace=1)))
    monkeypatch.setattr(
        User,
        "for_login",
        AsyncMock(side_effect=lambda _, login: {"ada": ada, "grace": grace}[login]),
    )
    monkeypatch.setattr(
        HumanReviewRequest, "open_review_counts", AsyncMock(return_value={ada.id: 0, grace.id: 0})
    )
    pull = MagicMock()
    pull.repo.can_write = AsyncMock(return_value=True)
    with caplog.at_level("INFO", logger=picking.__name__):
        choice = await picking._choose_reviewer(request, pull, None, {})
    assert isinstance(choice, picking.Pick) and choice.login == ("grace" if skip_reason else "ada")
    filtered = next(
        record for record in caplog.records if record.message == "Filtered reviewer candidates"
    )
    assert filtered.skipped == ({"ada": skip_reason} if skip_reason else {})
