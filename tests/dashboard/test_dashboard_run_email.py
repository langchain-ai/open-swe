"""PostgreSQL regressions for the email a run is attributed to."""

import pytest

from agent.threads import access as thread_access
from agent.users import User

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "johannes117")


async def test_run_email_prefers_the_users_row() -> None:
    await User.sign_in("github", "117", login="johannes117", email="johannes@langchain.dev")
    # OAuth profile carries a personal account that isn't an org member.
    profile = {"email": "johannesduplessis117@gmail.com"}

    assert await thread_access.resolve_run_email("johannes117", profile) == "johannes@langchain.dev"


async def test_run_email_falls_back_to_profile_for_an_unknown_login() -> None:
    profile = {"email": "someone@example.com"}

    assert await thread_access.resolve_run_email("nobody", profile) == "someone@example.com"
