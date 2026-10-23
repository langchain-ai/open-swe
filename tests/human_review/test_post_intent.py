from unittest.mock import AsyncMock

import pytest

from openswe.human_review import post_intent
from openswe.human_review.post_intent import PullRequestPost, _fit
from openswe.slack.payloads import SlackMessage


def test_fit_cuts_the_middle_of_long_messages_before_dropping_any() -> None:
    lines = ["a" * 50, "b" * 1000 + "END", ">>> post"]
    fitted = _fit(lines, 700)
    assert len(fitted) == 3 and sum(map(len, fitted)) + len(fitted) <= 700
    assert fitted[0] == lines[0] and fitted[2] == ">>> post"
    assert fitted[1].startswith("bbb") and fitted[1].endswith("END") and "[…]" in fitted[1]


def test_fit_drops_the_oldest_once_messages_would_be_cut_too_short() -> None:
    lines = [str(index) * 400 for index in range(10)] + [">>> post"]
    fitted = _fit(lines, 1000)
    assert fitted[-1] == ">>> post" and sum(map(len, fitted)) + len(fitted) <= 1000
    assert all(len(line) >= 200 for line in fitted[:-1]) and fitted[0][0] != "0"


async def test_top_level_posts_are_judged_with_the_channels_previous_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = AsyncMock(public=True)
    channel.messages = AsyncMock(
        return_value=[
            SlackMessage(ts="100.0", user="U2", text="anyone free to review today?"),
            SlackMessage(ts="200.0", user="U1", text="https://github.com/lc/repo/pull/7"),
        ]
    )
    monkeypatch.setattr(post_intent.SlackChannel, "load", AsyncMock(return_value=channel))
    monkeypatch.setattr(
        post_intent, "get_slack_user_names", AsyncMock(return_value={"U1": "Ada", "U2": "Grace"})
    )
    post = PullRequestPost("C1", "U1", "https://github.com/lc/repo/pull/7", "200.0", "200.0")
    assert await post._transcript() == (
        "Grace: anyone free to review today?\n>>> Ada: https://github.com/lc/repo/pull/7"
    )
    channel.messages.assert_awaited_once_with(100, oldest=f"{200 - 86400:.6f}", latest="200.0")
