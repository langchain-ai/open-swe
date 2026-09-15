from agent.dashboard.user_preferences import (
    UserPreferencesUpdate,
    get_user_preferences,
    set_user_preferences,
)
from tests.conftest import FakeStore


async def test_default_workspace_preference_round_trips(fake_store: FakeStore) -> None:
    assert (await get_user_preferences("alice"))["default_workspace"] is None
    await set_user_preferences(
        "alice", UserPreferencesUpdate(default_visibility="public", default_workspace=" OSS ")
    )
    assert (await get_user_preferences("alice"))["default_workspace"] == "oss"
