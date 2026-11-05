from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pytest import MonkeyPatch

from openswe.github.http import RepoClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review import routes
from openswe.human_review.requests import HumanReviewRequest, RequestKind


def test_review_channel_most_files_and_ties(monkeypatch: MonkeyPatch) -> None:
    settings = RepoSettings.model_validate(
        {
            "reviewChannel": "#fallback",
            "reviewChannelRules": [
                {"paths": ["ui/*"], "channel": "#frontend"},
                {"paths": ["agent/*"], "channel": "#backend"},
            ],
        }
    )
    assert settings.channel_for_files(["ui/a", "ui/nested/b", "agent/c"]) == "#frontend"
    choices: list[str] = []

    def choose(channels: list[str]) -> str:
        choices.extend(channels)
        return channels[-1]

    monkeypatch.setattr("openswe.github.repo_files.random.choice", choose)
    assert settings.channel_for_files(["ui/a", "agent/b", "README.md"]) == "#backend"
    assert set(choices) == {"#frontend", "#backend"}
    choices.clear()
    assert settings.channel_for_files(["ui/a", "ui/b", "agent/c", "README.md"]) == "#frontend"
    assert choices == ["#frontend"]
    assert settings.channel_for_files(["README.md"]) == ""
    assert RepoSettings(reviewChannel="#fallback").channel_for_files(["README.md"]) == "#fallback"


@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("filename", ["ui/button.tsx", "README.md"])
async def test_review_availability_uses_cached_settings(
    routed: bool, filename: str, github_app: AsyncMock
) -> None:
    settings = RepoSettings.model_validate(
        {
            "reviewChannel": "#fallback",
            "reviewChannelRules": ([{"paths": ["ui/*"], "channel": "#frontend"}] if routed else []),
        }
    )
    with (
        patch.object(routes, "require_repo_access_for_user", AsyncMock()),
        patch.object(RepoSettings, "cached", AsyncMock(return_value=settings)),
        patch.object(
            RepoClient, "read_file", AsyncMock(side_effect=AssertionError("uncached read"))
        ),
        patch.object(PullRequestClient, "head_sha", AsyncMock(return_value="abc")),
        patch.object(PullRequestClient, "files", AsyncMock(return_value=[{"filename": filename}])),
        patch.object(routes.HumanReviewRequest, "active_for", AsyncMock(return_value=None)),
        patch.object(routes, "review_blockers", AsyncMock(return_value=[])),
    ):
        result = await routes.api_human_review_availability("lc", "repo", 7, session={"sub": "ada"})
    assert result.available is (not routed or filename.startswith("ui/"))
    assert result.active is False


@pytest.mark.parametrize("denied", [False, True])
async def test_auto_merge_opt_out_requires_repository_access(denied: bool) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    with (
        patch.object(
            routes,
            "require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403, "Forbidden") if denied else None),
        ),
        patch.object(HumanReviewRequest, "active_for", AsyncMock(return_value=request)),
        patch.object(routes.ReviewCard, "disable_auto_merge", AsyncMock()) as disable,
    ):
        if denied:
            with pytest.raises(HTTPException) as error:
                await routes.api_disable_human_review_auto_merge(
                    "lc", "repo", 7, session={"sub": "ada"}
                )
            assert error.value.status_code == 403
            disable.assert_not_awaited()
        else:
            result = await routes.api_disable_human_review_auto_merge(
                "lc", "repo", 7, session={"sub": "ada"}
            )
            assert result.request_id == str(request.id)


@pytest.mark.parametrize("kind", ["standard", "expedited", "posted"])
async def test_auto_merge_opt_out_persists_without_removing_reviewers(
    registry_db: None, kind: RequestKind
) -> None:
    from openswe.database import postgres
    from openswe.human_review.requests import HumanReviewParticipant
    from openswe.users import User

    pr = await PullRequest(owner="lc", repo="repo", number=7).save()
    person = User()
    async with postgres.session() as session:
        session.add(person)
    request = await HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind=kind,
        participants=[HumanReviewParticipant(user_id=person.id, decision="review")],
    ).save()
    with patch.object(routes.ReviewCard, "refresh", AsyncMock()):
        if kind == "standard":
            await routes.ReviewCard(request).disable_auto_merge()
            await routes.ReviewCard(request).disable_auto_merge()
        else:
            with pytest.raises(ValueError):
                await routes.ReviewCard(request).disable_auto_merge()
    current = await HumanReviewRequest.active_for("lc", "repo", 7)
    assert current is not None
    assert current.auto_merge is (kind != "standard")
    assert [(p.user_id, p.decision) for p in current.participants] == [(person.id, "review")]
