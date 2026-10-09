from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx2
import pytest

from openswe.github.codeowners import CodeOwners
from openswe.github.pull_requests import PullRequest
from openswe.human_review.picking import ReviewerInstructions, WorkHours
from openswe.human_review.requests import HumanReviewRequest

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
    assert coverage.overlap("ada", "Grace")
    assert not coverage.overlap("ada", "default")
    assert coverage.satisfied("ada", ["Grace"])
    assert not coverage.satisfied("default", ["Grace"])
    assert not coverage.satisfied("nobody", ["Grace", "default"])


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


async def test_reviewer_instructions_include_only_files_inside_the_repository(
    monkeypatch: pytest.MonkeyPatch, github_app: AsyncMock
) -> None:
    from openswe.github import http as github_http

    files = {
        ".open-swe/REVIEWERS.md": (
            "Ask the owners below.\n@../.github/CODEOWNERS\n@/docs/teams.md\n"
            "@langchain-ai/security\n@../../secrets\n"
            "Mentions like @../.github/CODEOWNERS inside a sentence stay."
        ),
        ".github/CODEOWNERS": "/api/ @bob",
        "docs/teams.md": "Infra reviews deployments.",
    }
    fetched: list[str] = []

    async def respond(_client: object, method: str, url: str, **_: object) -> httpx2.Response:
        path = url.split("/contents/", 1)[1]
        fetched.append(path)
        request = httpx2.Request(method, url)
        if path in files:
            return httpx2.Response(200, text=files[path], request=request)
        return httpx2.Response(404, json={"message": "Not Found"}, request=request)

    monkeypatch.setattr(github_http, "github_request", respond)
    pr = PullRequest(owner="lc", repo="repo", number=7, base_ref="main")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr

    instructions = await ReviewerInstructions.load(request)

    assert instructions is not None
    assert instructions.text == (
        "Ask the owners below.\n"
        '<included_file name=".github/CODEOWNERS">\n/api/ @bob\n</included_file>\n'
        '<included_file name="docs/teams.md">\nInfra reviews deployments.\n</included_file>\n'
        "@langchain-ai/security\n@../../secrets\n"
        "Mentions like @../.github/CODEOWNERS inside a sentence stay."
    )
    assert not any("secrets" in path for path in fetched)


def test_off_shift_on_friday_evening_waits_for_monday_morning() -> None:
    hours = WorkHours(ZoneInfo("America/New_York"))
    friday_evening = datetime(2026, 10, 2, 23, tzinfo=UTC)
    assert not hours.on_shift(friday_evening)
    assert hours.next_start(friday_evening) == datetime(2026, 10, 5, 13, tzinfo=UTC)
