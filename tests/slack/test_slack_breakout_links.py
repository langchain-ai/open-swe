from unittest.mock import AsyncMock

import pytest

from openswe.slack import breakout_links
from openswe.slack.client import SlackThreadLocation
from tests.slack_helpers import at_location


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "permalink", "expected"),
    [
        ("C1", "https://slack/p200", "Continued in <https://slack/p200|the breakout thread>."),
        (
            "C2",
            None,
            "Continued in <https://slack.com/archives/C2/p2000|the breakout thread> in <#C2>.",
        ),
    ],
)
async def test_mark_broken_out_links_breakout_from_source_thread(
    monkeypatch, target, permalink, expected
):
    reply = AsyncMock()
    monkeypatch.setattr(breakout_links, "get_slack_permalink", AsyncMock(return_value=permalink))
    monkeypatch.setattr(SlackThreadLocation, "try_reply", at_location(reply))

    await breakout_links.mark_broken_out("C1", "100.0", target, "200.0")

    reply.assert_awaited_once_with(
        "C1",
        "100.0",
        expected,
        blocks=[{"type": "context", "elements": [{"type": "mrkdwn", "text": expected}]}],
        unfurl_links=False,
        unfurl_media=False,
    )
