from unittest.mock import AsyncMock

import pytest

from agent.slack import breakout_links


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
    react = AsyncMock()
    reply = AsyncMock()
    monkeypatch.setattr(breakout_links, "add_slack_reaction", react)
    monkeypatch.setattr(breakout_links, "get_slack_permalink", AsyncMock(return_value=permalink))
    monkeypatch.setattr(breakout_links, "post_slack_thread_reply", reply)

    await breakout_links.mark_broken_out("C1", "100.0", "105.0", target, "200.0")

    react.assert_awaited_once_with("C1", "105.0", breakout_links.BREAKOUT_REACTION)
    reply.assert_awaited_once_with("C1", "100.0", expected)
