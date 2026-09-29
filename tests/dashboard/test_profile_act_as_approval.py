"""PostgreSQL regressions for the act-as approval flag on the dashboard profile endpoint."""

import pytest

from agent.dashboard.profiles import ProfileUpdate, put_my_profile
from agent.users import User, UserPreferencesPatch
from tests.conftest import FakeStore

pytestmark = pytest.mark.usefixtures("registry_db")

_SESSION = {"sub": "ada", "email": "ada@example.com"}


@pytest.fixture(autouse=True)
def _authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


def _update(approval: bool | None) -> ProfileUpdate:
    return ProfileUpdate(
        default_model="openai:gpt-6.1-sol",
        reasoning_effort="high",
        experimental_act_as_approval=approval,
    )


async def test_switching_approval_clears_always_allow(fake_store: FakeStore) -> None:
    await User.sign_in("github", "1", login="ada")
    await User.update_preferences(
        "ada", UserPreferencesPatch(experimental_act_as_approval=True, act_as_always_allowed=True)
    )

    untouched = await put_my_profile(_update(None), _SESSION)
    assert untouched["act_as_always_allowed"] is True

    saved = await put_my_profile(_update(True), _SESSION)

    assert saved["experimental_act_as_approval"] is True
    assert saved["act_as_always_allowed"] is False
