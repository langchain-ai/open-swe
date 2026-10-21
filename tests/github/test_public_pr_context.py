from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe import pull_request_context as ctx
from openswe.review.author_guidance import HumanTurn, SteeringHistory
from openswe.run_config import RunConfig
from openswe.slack.channels import SlackChannel

_PUBLIC_CHANNEL: dict[str, Any] = {
    "id": "C1",
    "is_channel": True,
    "is_private": False,
    "is_im": False,
    "is_mpim": False,
    "is_ext_shared": False,
    "is_pending_ext_shared": False,
}


def _slack_config() -> RunConfig:
    return RunConfig.parse(
        {
            "source": "slack",
            "thread_id": "t1",
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
        }
    )


def _history() -> SteeringHistory:
    turn = HumanTurn(thread_id="t1", message_id="m1", author="u", text="fix it")
    return SteeringHistory(request=turn, follow_ups=[])


@pytest.mark.parametrize(
    "channel",
    [
        None,
        {**_PUBLIC_CHANNEL, "is_private": True},
        {**_PUBLIC_CHANNEL, "is_ext_shared": True},
        {"id": "D1", "is_im": True},
    ],
)
async def test_unshareable_slack_conversation_is_never_summarized(
    monkeypatch: pytest.MonkeyPatch, channel: dict[str, Any] | None
) -> None:
    row = SlackChannel.from_payload(channel) if channel else None
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=row))
    summarize = AsyncMock(return_value="leak")
    monkeypatch.setattr(ctx, "_summarize", summarize)

    assert await ctx.public_context_section(_slack_config()) is None
    summarize.assert_not_awaited()


async def test_public_channel_summary_is_collapsed_and_cannot_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SlackChannel, "load", AsyncMock(return_value=SlackChannel.from_payload(_PUBLIC_CHANNEL))
    )
    monkeypatch.setattr(SteeringHistory, "for_thread", AsyncMock(return_value=_history()))
    monkeypatch.setattr(ctx, "_summarize", AsyncMock(return_value="Ask @octo.</details><b>x</b>"))

    section = await ctx.public_context_section(_slack_config())

    assert section == ("<details>\n<summary>Context</summary>\n\nAsk @\u200bocto.x\n\n</details>")
