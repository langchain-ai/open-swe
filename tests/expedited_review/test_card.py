from unittest.mock import AsyncMock

import pytest

from openswe.expedited_review.card import _test_diffstat
from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review import lifecycle
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.blocks import SECTION_TEXT_MAX_CHARS


def test_long_test_paths_stay_under_the_slack_limit_and_count_the_rest() -> None:
    tests = [
        ChangedFile(filename=f"tests/{'deep/' * 28}test_{index}.py", additions=3, patch="+x")
        for index in range(20)
    ]

    [block] = _test_diffstat(tests)
    text = block["elements"][0]["text"]

    assert len(text) <= SECTION_TEXT_MAX_CHARS
    shown = text.count("test_")
    assert 0 < shown < len(tests)
    assert text.endswith(f"{len(tests) - shown} more test files on GitHub.")


async def test_configured_channel_hides_send_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    approval = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="expedited",
        slack_channel_choices=[{"id": "C1", "name": "kitchen"}],
    )
    approval.pull_request = pr
    monkeypatch.setattr(lifecycle, "repo_token", AsyncMock(return_value="token"))
    settings = RepoSettings(review_channel="C2")
    monkeypatch.setattr(RepoSettings, "cached", AsyncMock(return_value=settings))

    assert await lifecycle._channel_choices(approval) == []
    settings.review_channel = ""
    assert await lifecycle._channel_choices(approval) == [{"id": "C1", "name": "kitchen"}]
