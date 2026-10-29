from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from openswe.concierge.records import ConciergeBackground
from openswe.tools.concierge import concierge_background
from openswe.users import User
from openswe.users.models import UserIdentity


@pytest.mark.parametrize("direct", [True, False])
async def test_only_the_persons_own_run_changes_standing_instructions(
    monkeypatch: pytest.MonkeyPatch, grant_tool_access: Callable[..., None], direct: bool
) -> None:
    """A run woken by a background report reads the instructions but never rewrites them."""
    grant_tool_access(private=True, owner=True, direct=direct)
    user = User(identities=[UserIdentity(provider="slack", external_id="U1", login="ramon")])
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=user))
    slack_thread = {"channel_id": "D1", "thread_ts": "0", "triggering_user_id": "U1"}
    monkeypatch.setattr(
        "openswe.run_config.get_config", lambda: {"configurable": {"slack_thread": slack_thread}}
    )
    save = AsyncMock(return_value=ConciergeBackground(user_id=user.id, instructions="all of it"))
    monkeypatch.setattr(ConciergeBackground, "save_instructions", save)
    monkeypatch.setattr(ConciergeBackground, "for_user", AsyncMock(return_value=None))

    written = await concierge_background("do everything for me")
    read = await concierge_background()

    assert written["ok"] is direct
    assert save.await_count == (1 if direct else 0)
    assert read["ok"] is True
