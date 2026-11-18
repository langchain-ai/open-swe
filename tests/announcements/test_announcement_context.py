from xml.etree import ElementTree

from runtime_api.slack import SlackNotice

from openswe.announcements.delivery import announcement_blocks
from openswe.slack.webhook import _slack_context_input


def test_announcement_is_visible_when_a_private_thread_starts_from_its_reply() -> None:
    text = "Your conversations now count toward your Gateway quota."
    messages = SlackNotice.private_thread_messages(
        [
            {
                "ts": "1.0",
                "user": "UBOT",
                "bot_id": "B1",
                "text": text,
                "blocks": announcement_blocks(text),
            },
            {"ts": "2.0", "user": "U123", "text": "hello"},
            {"ts": "3.0", "user": "UBOT", "bot_id": "B1", "text": "Hi there"},
            {"ts": "4.0", "user": "U123", "text": "do you see the above"},
        ],
        lambda message: message.get("user") == "U123",
        bot_user_id="UBOT",
    )
    run_input = _slack_context_input(
        messages,
        {"U123": "Alice", "UBOT": "Robin"},
        {},
        channel={"id": "slack:D123", "platform": "slack"},
        bot_user_id="UBOT",
        event_ts="4.0",
        trigger_user_id="U123",
        request_text="do you see the above",
        request_blocks=[{"type": "text", "text": "do you see the above"}],
    )
    replayed = [
        ElementTree.fromstring(content)
        for message in run_input["messages"]
        if isinstance(content := message["content"], str) and content.startswith("<input-message")
    ]
    assert [(message.get("sender"), (message.text or "").strip()) for message in replayed] == [
        ("system:slack-notice", text),
        ("slack:U123", "hello"),
    ]
