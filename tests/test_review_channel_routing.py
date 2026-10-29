from unittest.mock import AsyncMock, patch

import pytest
from pytest import MonkeyPatch

from openswe.github.http import RepoClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.repo_files import RepoSettings
from openswe.human_review import routes


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
