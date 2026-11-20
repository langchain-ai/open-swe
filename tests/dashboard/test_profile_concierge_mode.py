"""PostgreSQL regressions for concierge mode on the dashboard profile endpoint."""

from contextlib import AsyncExitStack

import pytest

from openswe.dashboard.deps import DashboardRead
from openswe.dashboard.principal import AuthorizedClaims, DashboardPrincipal
from openswe.dashboard.profiles import ProfileUpdate, get_my_profile, put_my_profile
from openswe.database import postgres
from openswe.users import User, UserPreferences

pytestmark = pytest.mark.usefixtures("registry_db")

_SESSION = {"sub": "ada", "email": "ada@example.com"}


@pytest.fixture(autouse=True)
def _authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


@pytest.fixture
async def reader():
    person = await User.sign_in("github", "1", login="ada")
    async with AsyncExitStack() as scope:
        db = await scope.enter_async_context(postgres.session())
        yield DashboardRead(
            DashboardPrincipal.from_session(_SESSION),
            person,
            AuthorizedClaims(_SESSION, person),
            "ada@example.com",
            db,
            scope,
        )


async def test_turning_concierge_mode_on(reader: DashboardRead) -> None:
    assert (await get_my_profile(reader))["concierge_mode"] is False
    saved = await put_my_profile(ProfileUpdate(concierge_mode=True), reader)
    assert saved["concierge_mode"] is True
    await reader.close()
    assert await User.preferences_for_login("ada") == UserPreferences(concierge_mode=True)
