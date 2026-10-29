import importlib
from unittest.mock import AsyncMock

import pytest

from openswe.run_config import RunConfig
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
async def test_channel_mismatch_reports_existing_location(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openswe.human_review.standard import RequestResult

    monkeypatch.setattr(
        request_human_review,
        "get_config",
        lambda: {"configurable": {"source": source, "github_login": "alice", "thread_id": "owner"}},
    )
    monkeypatch.setattr(request_human_review, "_repository_refusal", AsyncMock(return_value=None))
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=User()))
    monkeypatch.setattr(
        request_human_review, "run_slack_location", AsyncMock(return_value=("C1", "1.0"))
    )
    monkeypatch.setattr(
        request_human_review,
        "request_review",
        AsyncMock(
            return_value=RequestResult(
                success=True,
                request_id="request",
                channel="C_OLD",
                reused=True,
                channel_mismatch=True,
                requested_channel="C_NEW",
            )
        ),
    )
    result = await request_human_review.request_human_review(
        "https://github.com/lc/repo/pull/7", "Summary", "#requested"
    )
    assert result["success"] is True
    assert result["request_id"] == "request"
    assert "<#C_OLD>" in result["next"] and "<#C_NEW>" in result["next"]
    assert "dismiss_human_review_request" in result["next"]
    assert "Do not announce" not in result["next"]
