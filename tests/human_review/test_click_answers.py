"""A review click's answer goes back through the click's ``response_url``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from openswe.github.pull_requests import PullRequest
from openswe.human_review import clicks
from openswe.human_review.people import Outcome
from openswe.human_review.requests import HumanReviewRequest

_RESPONSE_URL = "https://hooks.slack.com/actions/T1/1/abc"


@pytest.fixture
def request_row(monkeypatch: pytest.MonkeyPatch) -> HumanReviewRequest:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    row = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    row.pull_request = pr
    monkeypatch.setattr(HumanReviewRequest, "get", AsyncMock(return_value=row))

    @asynccontextmanager
    async def lock(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(clicks, "slack_thread_mutation_lock", lock)
    monkeypatch.setattr(clicks, "langgraph_client", lambda: None)
    return row


@pytest.mark.parametrize("response_url", [_RESPONSE_URL, ""])
async def test_the_clicker_hears_the_outcome_where_they_clicked(
    monkeypatch: pytest.MonkeyPatch, request_row: HumanReviewRequest, response_url: str
) -> None:
    respond = AsyncMock(return_value=True)
    bot = AsyncMock(return_value=True)
    monkeypatch.setattr(clicks, "respond_to_slack_interaction", respond)
    monkeypatch.setattr(clicks, "post_slack_ephemeral_message", bot)

    await clicks.answer_click(
        str(request_row.id),
        channel_id="C1",
        thread_ts="171.1",
        slack_user_id="U1",
        handle=AsyncMock(return_value=Outcome("You're down to review it.")),
        response_url=response_url,
    )

    if response_url:
        respond.assert_awaited_once_with(
            _RESPONSE_URL,
            {
                "response_type": "ephemeral",
                "replace_original": False,
                "text": "You're down to review it.",
            },
        )
        bot.assert_not_awaited()
    else:
        # A modal's submission has no response_url: the bot answers in the thread.
        bot.assert_awaited_once_with("C1", "U1", "You're down to review it.", "171.1")
        respond.assert_not_awaited()
