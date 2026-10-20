import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from openswe.github.pull_requests import PullRequest
from openswe.human_review import standard
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.run_config import RunConfig
from openswe.slack.channels import SlackChannel
from openswe.users import User

# ``openswe.tools`` exports the tool function under the module's name.
request_human_review = importlib.import_module("openswe.tools.request_human_review")


def _config(source: str = "slack") -> RunConfig:
    return RunConfig.parse(
        {
            "source": source,
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "1.0",
                "triggering_event_ts": "2.0",
                "triggering_user_id": "U_ALICE",
            },
        }
    )


@pytest.mark.parametrize(
    ("source", "message", "named"),
    [
        ("slack", {"user": "U_ALICE", "text": "<@U0BOT> assign <@U_BOB> please"}, True),
        ("slack", {"user": "U_ALICE", "text": "<@U0BOT> assign @Bob"}, True),
        ("slack", {"user": "U_ALICE", "text": "<@U0BOT> assign @bobby"}, False),
        ("slack", {"user": "U_ALICE", "text": "<@U0BOT> who is reviewing this?"}, False),
        ("slack", {"user": "U_MALLORY", "text": "assign <@U_BOB>"}, False),
        ("slack", {"user": "U_ALICE", "bot_id": "B1", "text": "assign <@U_BOB>"}, False),
        ("github", {"user": "U_ALICE", "text": "assign <@U_BOB>"}, False),
    ],
)
async def test_only_a_person_naming_the_reviewer_may_reassign(
    monkeypatch: pytest.MonkeyPatch, source: str, message: dict[str, str], named: bool
) -> None:
    bob = User()
    monkeypatch.setattr(type(bob), "slack_user_id", property(lambda _: "U_BOB"))
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=bob))
    monkeypatch.setattr(
        request_human_review, "fetch_slack_thread_message_by_ts", AsyncMock(return_value=message)
    )
    assert await request_human_review._named_by_trigger(_config(source), "bob") is named


@pytest.mark.parametrize("source", ["slack", "mcp"])
@pytest.mark.parametrize("channel", ["", "#old", "#new"])
async def test_existing_review_respects_explicit_channel(
    monkeypatch: pytest.MonkeyPatch, github_app: AsyncMock, source: str, channel: str
) -> None:
    requester = User()
    pr = PullRequest(owner="o", repo="r", number=1, author="ada")
    request = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="standard",
        requested_by_user_id=requester.id,
        slack_channel_id="C_OLD",
        slack_message_ts="1.0",
        tldr="Original summary",
    )
    request.pull_request = pr
    config = {"source": source, "github_login": "ada", "thread_id": "thread"}
    monkeypatch.setattr(request_human_review, "get_config", lambda: {"configurable": config})
    monkeypatch.setattr(request_human_review, "_repository_refusal", AsyncMock(return_value=None))
    monkeypatch.setattr(
        request_human_review, "run_slack_location", AsyncMock(return_value=("C_ORIGIN", "2.0"))
    )
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=requester))
    monkeypatch.setattr(HumanReviewRequest, "active_for", AsyncMock(return_value=request))
    monkeypatch.setattr(HumanReviewRequest, "get", AsyncMock(return_value=request))
    resolve = AsyncMock(return_value=SlackChannel(id="C_NEW" if channel == "#new" else "C_OLD"))
    monkeypatch.setattr(SlackChannel, "resolve", resolve)
    monkeypatch.setattr(standard, "_permalink", AsyncMock(return_value="https://slack/card"))
    refresh = AsyncMock()
    post = AsyncMock()
    monkeypatch.setattr(ReviewCard, "refresh", refresh)
    monkeypatch.setattr(ReviewCard, "post_standard", post)

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        yield None, request

    monkeypatch.setattr(HumanReviewRequest, "locked", locked)
    result = await request_human_review.request_human_review(pr.url, "New summary", channel)

    assert result["success"] is True
    assert result["request_id"] == str(request.id)
    post.assert_not_awaited()
    if channel:
        resolve.assert_awaited_once_with(channel)
    else:
        resolve.assert_not_awaited()
    if channel == "#new":
        assert result["channel_mismatch"] is True
        assert result["slack_channel_id"] == "C_OLD"
        assert result["requested_channel"] == "C_NEW"
        assert "<#C_OLD>" in result["next"]
        assert "<#C_NEW>" in result["next"]
        assert "dismiss_human_review_request" in result["next"]
        assert "Do not announce" not in result["next"]
        assert request.tldr == "Original summary"
        refresh.assert_not_awaited()
    else:
        assert not result.get("channel_mismatch", False)
        assert request.tldr == "New summary"
        refresh.assert_awaited_once()
