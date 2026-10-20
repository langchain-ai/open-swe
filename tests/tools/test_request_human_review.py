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
        ("slack", {"user": "U_ALICE", "text": "reassign reviewer to Robert"}, True),
        ("slack", {"user": "U_ALICE", "text": "reassign reviewer to robert smith"}, True),
        ("slack", {"user": "U_ALICE", "text": "assign bob"}, True),
        ("slack", {"user": "U_ALICE", "text": "assign Robertson"}, False),
        ("slack", {"user": "U_ALICE", "text": "assign @not-bob"}, False),
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
    bob = User(display_name="Robert Smith")
    monkeypatch.setattr(type(bob), "slack_user_id", property(lambda _: "U_BOB"))
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=bob))
    monkeypatch.setattr(
        request_human_review, "fetch_slack_thread_message_by_ts", AsyncMock(return_value=message)
    )
    assert await request_human_review._named_by_trigger(_config(source), "bob") is named


@pytest.mark.parametrize(
    ("previous", "named"),
    [
        ({"user": "U_ALICE", "text": "reassign reviewer to Ramon"}, True),
        ({"user": "U_MALLORY", "text": "reassign reviewer to Ramon"}, False),
        ({"user": "U_ALICE", "bot_id": "B1", "text": "assign Ramon"}, False),
        ({"user": "U_ALICE", "text": "who is reviewing this?"}, False),
    ],
)
async def test_bot_ping_uses_only_the_same_persons_immediately_preceding_message(
    monkeypatch: pytest.MonkeyPatch, previous: dict[str, str], named: bool
) -> None:
    monkeypatch.setattr(
        User, "for_login", AsyncMock(return_value=User(display_name="Ramon Nogueira"))
    )
    monkeypatch.setattr(
        request_human_review,
        "fetch_slack_thread_message_by_ts",
        AsyncMock(return_value={"user": "U_ALICE", "text": "<@U0BOT>"}),
    )
    monkeypatch.setattr(
        request_human_review,
        "fetch_slack_thread_messages",
        AsyncMock(
            return_value=[
                {"ts": "1.0", **previous},
                {"ts": "2.0", "user": "U_ALICE", "text": "<@U0BOT>"},
                {"ts": "3.0", "user": "U_ALICE", "text": "assign someone else"},
            ]
        ),
    )
    assert await request_human_review._named_by_trigger(_config(), "ramon-langchain") is named
