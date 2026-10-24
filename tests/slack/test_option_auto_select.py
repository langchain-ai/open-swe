from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.slack import option_auto_select
from openswe.slack.blocks import option_actions
from openswe.slack.option_auto_select import PendingOption, claim_option_choice

_PENDING = PendingOption(
    channel_id="C1", thread_ts="1.0", message_ts="2.0", option="Proceed", user_id="U1"
)


def _thread(*later: dict[str, Any]) -> list[dict[str, Any]]:
    posted = {
        "ts": "2.0",
        "bot_id": "B1",
        "text": "Proceed?",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "Proceed?"}},
            *option_actions(["Proceed"], recommended="Proceed"),
        ],
    }
    return [{"ts": "1.0", "user": "U1", "text": "Do it"}, posted, *later]


@pytest.mark.usefixtures("registry_db")
@pytest.mark.parametrize(
    ("later", "clicked", "chosen"),
    [
        ((), False, True),
        (({"ts": "3.0", "user": "U1", "text": "Wait"},), False, False),
        ((), True, False),
    ],
)
async def test_recommended_option_is_chosen_only_while_nobody_answered(
    monkeypatch: pytest.MonkeyPatch,
    later: tuple[dict[str, Any], ...],
    clicked: bool,
    chosen: bool,
) -> None:
    monkeypatch.setattr(
        option_auto_select, "fetch_slack_thread_messages", AsyncMock(return_value=_thread(*later))
    )
    update = AsyncMock()
    wake = AsyncMock()
    monkeypatch.setattr(option_auto_select, "update_slack_message", update)
    monkeypatch.setattr("openswe.slack.thread_owner.wake_thread_owner", wake)
    if clicked:
        assert await claim_option_choice("C1", "2.0")

    assert await _PENDING.choose() is chosen

    if not chosen:
        wake.assert_not_awaited()
        update.assert_not_awaited()
        return
    assert wake.await_args.args[:3] == ("C1", "1.0", "U1")
    blocks = update.await_args.kwargs["blocks"]
    assert [block["type"] for block in blocks] == ["section", "context"]
    assert not await claim_option_choice("C1", "2.0")
