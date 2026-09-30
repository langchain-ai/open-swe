"""PostgreSQL regressions for moving the legacy concierge opt-in into ``users.preferences``."""

import pytest

from agent.users import User, UserPreferences, UserPreferencesPatch
from agent.users.import_concierge_mode import PROFILES_NAMESPACE, import_concierge_mode
from tests.conftest import FakeStore

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada,bob,carol")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


async def test_opt_in_moves_to_postgres_and_leaves_the_rest_of_the_profile(
    fake_store: FakeStore,
) -> None:
    await User.sign_in("github", "1", login="ada")
    fake_store.seed(
        PROFILES_NAMESPACE, "ada", {"login": "ada", "dm_session_enabled": True, "draft_prs": False}
    )

    assert await import_concierge_mode() == 1

    assert await User.preferences_for_login("ada") == UserPreferences(concierge_mode=True)
    assert fake_store.values(PROFILES_NAMESPACE)["ada"] == {"login": "ada", "draft_prs": False}
    assert await import_concierge_mode() == 0


async def test_a_choice_already_saved_in_postgres_wins(fake_store: FakeStore) -> None:
    await User.sign_in("github", "2", login="bob")
    await User.update_preferences("bob", UserPreferencesPatch(concierge_mode=False))
    fake_store.seed(PROFILES_NAMESPACE, "bob", {"login": "bob", "dm_session_enabled": True})

    assert await import_concierge_mode() == 1

    assert await User.preferences_for_login("bob") == UserPreferences(concierge_mode=False)
    assert "dm_session_enabled" not in fake_store.values(PROFILES_NAMESPACE)["bob"]


async def test_opt_in_without_a_users_row_waits_for_a_later_startup(
    fake_store: FakeStore,
) -> None:
    fake_store.seed(PROFILES_NAMESPACE, "carol", {"login": "carol", "dm_session_enabled": True})

    assert await import_concierge_mode() == 0
    assert fake_store.values(PROFILES_NAMESPACE)["carol"]["dm_session_enabled"] is True

    await User.sign_in("github", "3", login="carol")
    assert await import_concierge_mode() == 1
    assert await User.preferences_for_login("carol") == UserPreferences(concierge_mode=True)
