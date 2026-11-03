from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openswe.review_guide import context
from openswe.review_guide.launch import GUIDE_RUN_SOURCE


@pytest.mark.parametrize("source", ["dashboard", GUIDE_RUN_SOURCE])
async def test_only_a_persons_own_dashboard_message_acts_for_them(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    cfg = SimpleNamespace(source=source, github_login="alice", slack_thread=None)
    monkeypatch.setattr(context.RunConfig, "from_runtime", Mock(return_value=cfg))

    if source == "dashboard":
        assert await context.requester_login() == "alice"
        return
    with pytest.raises(context.GuideUnavailableError, match="person's own message"):
        await context.requester_login()
