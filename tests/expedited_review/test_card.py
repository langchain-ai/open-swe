from unittest.mock import AsyncMock

import pytest

from openswe.expedited_review.card import _diff_sections, _test_diffstat
from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review import lifecycle
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.blocks import SECTION_TEXT_MAX_CHARS
from openswe.slack.client import SlackRequestError


def test_missing_image_refuses_text_diff(caplog: pytest.LogCaptureFixture) -> None:
    files = [ChangedFile(filename="app.py", additions=1, patch="+secret_patch")]
    with pytest.raises(SlackRequestError, match="missing_expedited_diff_image"):
        _diff_sections(files, None)
    assert "without a diff image" in caplog.text
    assert "secret_patch" not in caplog.text


@pytest.mark.parametrize("failure", ["render", "upload", "processing"])
async def test_image_failure_is_logged_and_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    approval = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="expedited")
    files = [ChangedFile(filename="app.py", additions=1, patch="+x")]
    monkeypatch.setattr(lifecycle, "render_diff_png", lambda _: b"png")
    monkeypatch.setattr(lifecycle, "upload_slack_thread_file", AsyncMock(return_value="F1"))
    monkeypatch.setattr(lifecycle, "wait_for_slack_file", AsyncMock(return_value=True))
    if failure == "render":

        def fail_render(_: object) -> bytes:
            raise ValueError("render failed")

        monkeypatch.setattr(lifecycle, "render_diff_png", fail_render)
    elif failure == "upload":
        monkeypatch.setattr(
            lifecycle,
            "upload_slack_thread_file",
            AsyncMock(side_effect=SlackRequestError("upload_failed")),
        )
    else:
        monkeypatch.setattr(lifecycle, "wait_for_slack_file", AsyncMock(return_value=False))
    with pytest.raises(SlackRequestError):
        await lifecycle._diff_image_id(approval, files)
    assert any(record.levelname == "ERROR" for record in caplog.records)


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
