import pytest
from sqlalchemy import text

from openswe.dashboard.agent_instructions import AGENT_INSTRUCTIONS
from openswe.dashboard.import_agent_instructions import import_store_agent_instructions
from openswe.database.postgres import transaction
from openswe.database.store_imports import StoreImport
from openswe.users.models import User
from tests.conftest import FakeStore

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_import_copies_then_retires_store_records(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "octo")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    await User.sign_in("github", "1", login="octo")
    namespace = ["agent_instructions"]
    fake_store.seed(
        namespace, "acme/repo", {"instructions": "old", "updated_at": "2026-01-01T00:00:00+00:00"}
    )
    fake_store.seed(
        namespace,
        "Acme/Repo",
        {"instructions": "new", "created_by": "octo", "updated_at": "2026-02-01T00:00:00+00:00"},
    )
    fake_store.seed(namespace, "not-a-repo", {"instructions": "kept"})

    assert await import_store_agent_instructions() == StoreImport(moved=1)
    record = await AGENT_INSTRUCTIONS.get("acme/repo")
    assert record is not None and record.instructions == "new"
    assert record.created_by == "octo"
    assert await import_store_agent_instructions() == StoreImport(waiting=2)
    assert len(fake_store.values(namespace)) == 3

    async with transaction() as conn:
        await conn.execute(
            text("UPDATE agent_instructions SET updated_at = updated_at - interval '2 days'")
        )
    assert await import_store_agent_instructions() == StoreImport(moved=2)
    assert list(fake_store.values(namespace)) == ["not-a-repo"]
    assert await import_store_agent_instructions() == StoreImport()
