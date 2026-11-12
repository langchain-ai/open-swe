from unittest.mock import AsyncMock

import pytest
from runtime_api.slack import SlackNotice

from openswe.slack import breakout_links


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "permalink", "expected"),
    [
        ("C1", "https://slack/p200", "Continued in <https://slack/p200|the breakout thread>."),
        (
            "C2",
            None,
            "Continued in <https://robin.example/dashboard/api/slack/open?channel=C2&message_ts=200.0|the breakout thread> in <#C2>.",
        ),
    ],
)
async def test_mark_broken_out_links_breakout_from_source_thread(
    monkeypatch, target, permalink, expected
):
    monkeypatch.setattr(
        "openswe.slack.links.dashboard_api_base_url", lambda: "https://robin.example"
    )
    reply = AsyncMock()
    monkeypatch.setattr(breakout_links, "get_slack_permalink", AsyncMock(return_value=permalink))
    monkeypatch.setattr(breakout_links, "post_slack_thread_reply", reply)

    await breakout_links.mark_broken_out("C1", "100.0", target, "200.0")

    reply.assert_awaited_once()
    assert reply.await_args is not None
    args, kwargs = reply.await_args
    assert args == ("C1", "100.0", expected)
    # A notice: the source thread's later turns read it from Slack with the command.
    assert SlackNotice.marks(kwargs)
    assert kwargs["blocks"][0]["elements"] == [{"type": "mrkdwn", "text": expected}]
