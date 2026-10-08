import pytest

from openswe.dashboard.profiles import PROFILES
from openswe.database.store_imports import StoreImport, run_store_import
from openswe.users.import_records import import_user_records
from openswe.users.models import User
from tests.conftest import FakeStore

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_import_moves_known_people_and_keeps_postgres_values(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada,bob")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    await User.sign_in("github", "1", login="Ada")
    await User.sign_in("github", "2", login="bob")
    await PROFILES.put("bob", {"draft_prs": True})
    fake_store.seed(["profiles"], "Ada", {"draft_prs": False})
    fake_store.seed(["profiles"], "bob", {"draft_prs": False})
    fake_store.seed(["profiles"], "carol", {"draft_prs": False})

    assert await import_user_records() == StoreImport(moved=2, waiting=1)

    assert await PROFILES.get("ada") == {"draft_prs": False}
    assert await PROFILES.get("bob") == {"draft_prs": True}
    assert list(fake_store.values(["profiles"])) == ["carol"]


async def test_an_import_runs_until_a_pass_finds_nothing_left() -> None:
    passes = [StoreImport(moved=3), StoreImport(waiting=1), StoreImport(), StoreImport(moved=9)]

    async def run() -> StoreImport:
        return passes.pop(0)

    for _ in range(4):
        await run_store_import("test", run)

    assert passes == [StoreImport(moved=9)]
