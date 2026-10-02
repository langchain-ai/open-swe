from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from agent.expedited_review import modal
from agent.expedited_review.eligibility import ChangedFile
from agent.slack.payloads import SlackInteraction


def test_review_pages_pin_all_files_including_tests() -> None:
    origin = modal.ReviewOrigin(
        approval_id=uuid4(), channel_id="C1", thread_ts="1.0", user_id="U1", fingerprint="source"
    )
    files = [ChangedFile(filename=name, patch="+x") for name in ["a_test.py", "b.py", "c.py"]]
    assert modal.pin_files(origin, files)
    assert not modal.pin_files(origin, files[1:])
    view = modal.render_page(origin, files, "https://github.com/lc/repo/pull/1", "lc/repo", 1)
    assert view is not None
    assert view["submit"]["text"] == "Next file"
    origin.page = 2
    view = modal.render_page(origin, files, "https://github.com/lc/repo/pull/1", "lc/repo", 1)
    assert view is not None
    assert view["submit"]["text"] == "Approve"


async def test_submission_acknowledges_before_fetching_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_page(origin: modal.ReviewOrigin) -> None:
        pytest.fail("GitHub must not be fetched before acknowledging Slack")

    monkeypatch.setattr(modal, "review_page", slow_page)
    origin = modal.ReviewOrigin(
        approval_id=uuid4(), channel_id="C1", thread_ts="1.0", user_id="U1", fingerprint="source"
    )
    interaction = SlackInteraction.model_validate(
        {"user": {"id": "U1"}, "view": {"id": "V1", "private_metadata": origin.model_dump_json()}}
    )
    tasks = BackgroundTasks()
    result = await modal.submit_page(interaction, tasks)
    assert result["response_action"] == "update"
    assert len(tasks.tasks) == 1
