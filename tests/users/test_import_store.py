"""PostgreSQL regressions for moving legacy user mappings into ``users``."""

from unittest.mock import AsyncMock

import pytest

from agent.users import User, import_store
from agent.users.import_store import USER_MAPPINGS_NAMESPACE, import_user_mappings
from tests.conftest import FakeStore

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "OctoCat,grace,ghost")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    monkeypatch.setattr(
        import_store, "get_github_app_installation_token", AsyncMock(return_value="tok")
    )


def _mapping(login: str, email: str = "", slack_user_id: str | None = None) -> dict[str, object]:
    return {
        "github_login": login,
        "work_email": email,
        "slack_user_id": slack_user_id,
        "source": "slack_oauth",
        "status": "active",
    }


async def test_creates_the_user_from_a_mapping_and_deletes_the_record(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(USER_MAPPINGS_NAMESPACE, "octocat", _mapping("OctoCat", "octo@x.com", "U1"))
    monkeypatch.setattr(
        import_store,
        "_github_account",
        AsyncMock(return_value=import_store._GithubAccount(id=1001, login="OctoCat")),
    )

    assert await import_user_mappings() == 1

    user = await User.for_identity("slack", "U1")
    assert user is not None
    assert (user.github_login, user.email) == ("OctoCat", "octo@x.com")
    assert await User.for_identity("github", "1001") is not None
    assert fake_store.values(USER_MAPPINGS_NAMESPACE) == {}


async def test_links_onto_an_existing_user_without_asking_github(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = await User.sign_in("github", "2002", login="grace")
    fake_store.seed(USER_MAPPINGS_NAMESPACE, "grace", _mapping("grace", "grace@x.com", "U2"))
    lookup = AsyncMock()
    monkeypatch.setattr(import_store, "_github_account", lookup)

    assert await import_user_mappings() == 1

    user = await User.for_identity("slack", "U2")
    assert user is not None and user.id == existing.id
    assert user.email == "grace@x.com"
    lookup.assert_not_awaited()


async def test_records_that_cannot_be_completed_wait_for_the_next_startup(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(USER_MAPPINGS_NAMESPACE, "ghost", _mapping("ghost", "g@x.com"))
    fake_store.seed(USER_MAPPINGS_NAMESPACE, "mallory", _mapping("mallory", "m@x.com"))

    async def account(login: str, token: str) -> import_store._GithubAccount | None:
        return None if login == "ghost" else import_store._GithubAccount(id=3003, login=login)

    monkeypatch.setattr(import_store, "_github_account", account)

    assert await import_user_mappings() == 0

    assert set(fake_store.values(USER_MAPPINGS_NAMESPACE)) == {"ghost", "mallory"}
    assert await User.for_login("github", "mallory") is None


async def test_a_work_email_with_no_slack_id_lands_on_the_github_identity(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await User.sign_in("github", "4004", login="grace")
    fake_store.seed(USER_MAPPINGS_NAMESPACE, "grace", _mapping("grace", "grace@work.example"))
    monkeypatch.setattr(import_store, "_github_account", AsyncMock())

    assert await import_user_mappings() == 1

    assert await User.email_for_login("grace") == "grace@work.example"
    assert fake_store.values(USER_MAPPINGS_NAMESPACE) == {}


async def test_a_work_email_that_cannot_be_stored_keeps_its_record(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record is the only copy of the work address, so success would delete it."""
    await User.sign_in("github", "5005", login="grace", email="grace@personal.example")
    fake_store.seed(USER_MAPPINGS_NAMESPACE, "grace", _mapping("grace", "grace@work.example"))
    monkeypatch.setattr(import_store, "_github_account", AsyncMock())

    assert await import_user_mappings() == 0

    assert set(fake_store.values(USER_MAPPINGS_NAMESPACE)) == {"grace"}
    assert await User.email_for_login("grace") == "grace@personal.example"


async def test_an_empty_namespace_is_a_noop(fake_store: FakeStore) -> None:
    assert await import_user_mappings() == 0
