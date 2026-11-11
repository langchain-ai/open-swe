import pytest

from openswe.slack import ask_messages
from openswe.slack.ask_messages import AskChannelMessage
from openswe.slack.payloads import SlackChannelContext, SlackMessage
from openswe.utils.jev import JevDecision
from openswe.utils.json_types import JsonObject


@pytest.mark.parametrize(
    ("ask_channel", "choice", "reaction", "reaction_succeeds", "allowed", "posted_emoji"),
    [
        (False, None, None, True, True, None),
        (True, "follow_up_only", "eyes", True, True, None),
        (True, "do_nothing", "eyes", True, False, None),
        (True, "react_only", "woman-tipping-hand", True, False, "woman-tipping-hand"),
        (True, "react_and_follow_up", "saluting_face", True, True, "saluting_face"),
        (True, "react_only", None, True, False, "eyes"),
        (True, "react_only", "invalid", True, False, "eyes"),
        (True, "react_only", "eyes", False, True, "eyes"),
        (True, "react_only", None, False, True, "eyes"),
        (True, "react_and_follow_up", None, True, True, "eyes"),
        (True, "react_and_follow_up", "eyes", False, True, "eyes"),
        (True, None, "eyes", True, False, None),
        (True, "failure", None, True, True, None),
    ],
)
async def test_ask_channel_messages_need_jev(
    monkeypatch: pytest.MonkeyPatch,
    ask_channel: bool,
    choice: str | None,
    reaction: str | None,
    reaction_succeeds: bool,
    allowed: bool,
    posted_emoji: str | None,
) -> None:
    async def is_ask_channel(_channel_id: str) -> bool:
        return ask_channel

    async def messages(_channel_id: str, _thread_ts: str, _message_ts: str) -> list[SlackMessage]:
        return [SlackMessage(ts="2.0", text="dude shut up robin")]

    reactions: list[tuple[str, str, str]] = []

    async def react(channel_id: str, message_ts: str, emoji: str) -> bool:
        reactions.append((channel_id, message_ts, emoji))
        return reaction_succeeds

    async def select(
        *_args: object, decisions: dict[str, JevDecision], **_kwargs: object
    ) -> dict[str, str | None]:
        decisions["action"].outcome = (
            "classifier_failure"
            if choice == "failure"
            else "accepted"
            if choice
            else "low_confidence"
        )
        return {"action": None if choice == "failure" else choice, "reaction": reaction}

    monkeypatch.setattr(ask_messages.WORKSPACES, "is_ask_channel", is_ask_channel)
    monkeypatch.setattr(AskChannelMessage, "_messages", staticmethod(messages))

    async def channel_context(_channel_id: str) -> SlackChannelContext:
        return SlackChannelContext(name="help")

    monkeypatch.setattr(ask_messages, "select_jev_choices", select)
    monkeypatch.setattr(ask_messages, "add_slack_reaction", react)
    monkeypatch.setattr(ask_messages.common, "resolve_slack_channel_context", channel_context)

    assert (
        await AskChannelMessage.for_robin(channel_id="C1", thread_ts="1.0", message_ts="2.0")
        is allowed
    )
    assert reactions == ([("C1", "2.0", posted_emoji)] if posted_emoji else [])


@pytest.mark.parametrize(
    ("message", "allowed"),
    [
        ({"files": [{"mimetype": "image/png"}]}, True),
        ({"files": [{"mimetype": "application/pdf"}]}, True),
        ({"attachments": [{"is_share": True, "text": "forwarded"}]}, True),
        (
            {
                "attachments": [
                    {"from_url": "https://example.com", "image_url": "https://example.com/a.png"}
                ]
            },
            False,
        ),
        ({"files": [], "attachments": []}, False),
    ],
)
async def test_ask_attachments_bypass_jev(
    monkeypatch: pytest.MonkeyPatch, message: JsonObject, allowed: bool
) -> None:
    async def is_ask_channel(_channel_id: str) -> bool:
        return True

    async def fetch(_channel_id: str, _thread_ts: str) -> list[dict[str, object]]:
        return [
            {"ts": "1.0", "files": [{"mimetype": "image/png"}]},
            {"ts": "2.0", "text": "see this", **message},
        ]

    async def channel_context(_channel_id: str) -> SlackChannelContext:
        return SlackChannelContext(name="help")

    classified: list[bool] = []

    async def select(*_args: object, **_kwargs: object) -> dict[str, str | None]:
        classified.append(True)
        return {"action": "do_nothing", "reaction": None}

    monkeypatch.setattr(ask_messages.WORKSPACES, "is_ask_channel", is_ask_channel)
    monkeypatch.setattr(ask_messages, "fetch_slack_thread_messages", fetch)
    monkeypatch.setattr(ask_messages.common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(ask_messages, "select_jev_choices", select)

    assert (
        await AskChannelMessage.for_robin(channel_id="C1", thread_ts="1.0", message_ts="2.0")
        is allowed
    )
    assert bool(classified) is not allowed
