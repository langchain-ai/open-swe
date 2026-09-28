"""PostgreSQL regressions for concierge mode on the dashboard profile endpoint."""

import pytest
from fastapi import HTTPException

from agent.dashboard.profiles import ProfileUpdate, put_my_profile
from agent.users import User, UserPreferences
from tests.conftest import FakeStore

pytestmark = pytest.mark.usefixtures("registry_db")

_SESSION = {"sub": "ada", "email": "ada@example.com"}


@pytest.fixture(autouse=True)
def _authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


def _update(concierge_mode: bool | None) -> ProfileUpdate:
    return ProfileUpdate(
        default_model="openai:gpt-6-sol", reasoning_effort="high", concierge_mode=concierge_mode
    )


async def test_saving_the_default_back_works_without_a_users_row(fake_store: FakeStore) -> None:
    saved = await put_my_profile(_update(False), _SESSION)

    assert saved["concierge_mode"] is False
    assert "dm_session_enabled" not in saved


async def test_turning_concierge_mode_on_needs_a_users_row(fake_store: FakeStore) -> None:
    with pytest.raises(HTTPException) as exc:
        await put_my_profile(_update(True), _SESSION)
    assert exc.value.status_code == 409
    assert fake_store.values(["profiles"]) == {}

    await User.sign_in("github", "1", login="ada")
    saved = await put_my_profile(_update(True), _SESSION)

    assert saved["concierge_mode"] is True
    assert await User.preferences_for_login("ada") == UserPreferences(concierge_mode=True)
