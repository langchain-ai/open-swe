import importlib
from unittest.mock import AsyncMock

import pytest

from openswe.run_config import RunConfig

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
    ("source", "message", "started"),
    [
        ("slack", {"user": "U_ALICE", "text": "reassign reviewer to Ramon"}, True),
        ("slack", None, False),
        ("slack", {"user": "U_MALLORY", "text": "assign Ramon"}, False),
        ("slack", {"user": "U_ALICE", "bot_id": "B1", "text": "assign Ramon"}, False),
        ("github", {"user": "U_ALICE", "text": "assign Ramon"}, False),
    ],
)
async def test_only_a_persons_own_slack_message_may_name_a_reviewer(
    monkeypatch: pytest.MonkeyPatch, source: str, message: dict[str, str] | None, started: bool
) -> None:
    monkeypatch.setattr(
        request_human_review, "fetch_slack_thread_message_by_ts", AsyncMock(return_value=message)
    )
    assert await request_human_review._started_by_person(_config(source)) is started
