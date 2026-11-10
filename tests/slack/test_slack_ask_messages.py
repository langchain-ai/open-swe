import pytest

from openswe.slack import ask_messages
from openswe.slack.ask_messages import AskChannelMessage
from openswe.slack.payloads import SlackChannelContext
from openswe.utils.jev import JevDecision


@pytest.mark.parametrize(
    ("ask_channel", "choice", "allowed"),
    [
        (False, None, True),
        (True, "addressed_to_robin", True),
        (True, "human_conversation", False),
        (True, None, False),
        (True, "failure", True),
    ],
)
async def test_ask_channel_messages_need_jev(
    monkeypatch: pytest.MonkeyPatch, ask_channel: bool, choice: str | None, allowed: bool
) -> None:
    async def is_ask_channel(_channel_id: str) -> bool:
        return ask_channel

    async def transcript(_channel_id: str, _thread_ts: str, _message_ts: str) -> str:
        return "<@U1> (newest message, to judge): dude shut up robin"

    async def select(*_args: object, decision: JevDecision, **_kwargs: object) -> str | None:
        decision.outcome = "classifier_failure" if choice == "failure" else "low_confidence"
        return None if choice == "failure" else choice

    monkeypatch.setattr(ask_messages.WORKSPACES, "is_ask_channel", is_ask_channel)
    monkeypatch.setattr(AskChannelMessage, "_transcript", staticmethod(transcript))

    async def channel_context(_channel_id: str) -> SlackChannelContext:
        return SlackChannelContext(name="help")

    monkeypatch.setattr(ask_messages, "select_jev_choice", select)
    monkeypatch.setattr(ask_messages.common, "resolve_slack_channel_context", channel_context)

    assert (
        await AskChannelMessage.for_robin(channel_id="C1", thread_ts="1.0", message_ts="2.0")
        is allowed
    )
