"""An escalated triage run can only watch pull requests the post itself linked."""

from importlib import import_module
from unittest.mock import AsyncMock

import pytest

from openswe.run_config import PullRequestPostRef, RunConfig
from openswe.users import User

tool = import_module("openswe.tools.decide_pull_request_post")

_POST = PullRequestPostRef(
    channel_id="C1",
    message_ts="1.0",
    slack_user_id="U1",
    text="<https://github.com/lc/repo/pull/7|#7> and https://github.com/lc/repo/pull/8",
)


@pytest.fixture
def watch(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(tool.RunConfig, "from_runtime", lambda: RunConfig(pull_request_post=_POST))
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=User()))
    monkeypatch.setattr(tool, "remove_slack_reaction", AsyncMock(return_value=True))
    watch = AsyncMock()
    monkeypatch.setattr(tool, "watch_pull_requests", watch)
    return watch


async def test_watches_chosen_linked_pull_requests_and_clears_eyes(watch: AsyncMock) -> None:
    result = await tool.decide_pull_request_post(["https://github.com/LC/repo/pull/8/"])
    assert result == {"success": True, "watching": ["https://github.com/lc/repo/pull/8"]}
    assert [ref.number for ref in watch.await_args.args[3]] == [8]
    tool.remove_slack_reaction.assert_awaited_once_with("C1", "1.0", "eyes")


async def test_refuses_pull_requests_the_post_did_not_link(watch: AsyncMock) -> None:
    result = await tool.decide_pull_request_post(["https://github.com/lc/other/pull/1"])
    assert result["success"] is False
    watch.assert_not_called()
