"""MCP connections in PostgreSQL, and the one-off import from the LangGraph Store."""

import logging
from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update

from agent.database import postgres
from agent.mcp.models import MCPConnection
from agent.mcp.rows import UserMCPConnectionRow, WorkspaceMCPConnectionRow
from agent.mcp.store import MCPConnectionStore, import_store_records
from agent.mcp.user import USER_MCPS_NAMESPACE
from agent.mcp.workspace import WORKSPACE_MCPS_NAMESPACE
from agent.store import now_iso
from agent.users import User
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG
from tests.conftest import FakeStore

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob,acme,other")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


def connection(name: str, **fields: object) -> MCPConnection:
    return MCPConnection(
        **{
            "name": name,
            "url": f"https://{name}.example/mcp",
            "revision": uuid4().hex,
            "updated_at": now_iso(),
            **fields,
        }
    )


async def make_user(login: str) -> UUID:
    """The ``users`` row a personal connection hangs off."""
    return (await User.sign_in("github", login, login=login)).id


async def stored_user_row(login: str, name: str) -> UserMCPConnectionRow | None:
    user = await User.for_login("github", login)
    if user is None:
        return None
    async with postgres.session() as session:
        return await session.scalar(
            select(UserMCPConnectionRow).where(
                UserMCPConnectionRow.user_id == user.id, UserMCPConnectionRow.name == name
            )
        )


async def stored_user_names(user_id: UUID) -> list[str]:
    async with postgres.session() as session:
        names = await session.scalars(
            select(UserMCPConnectionRow.name).where(UserMCPConnectionRow.user_id == user_id)
        )
        return sorted(names)


@pytest.fixture
def prefix_store(fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """``fake_store``, matching namespaces by prefix and reporting them like a real backend.

    A Store search is a prefix match, so scanning ``["workspace_mcps"]`` also
    surfaces the per-owner namespaces nested under it; the in-memory double
    matches exactly, which would hide that from the import.
    """

    async def search_items(
        namespace: Sequence[str],
        *,
        filter: dict[str, object] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, object]:
        prefix = tuple(namespace)
        matches = [
            {"namespace": list(stored), "value": dict(value)}
            for stored, items in sorted(fake_store.items.items())
            for value in items.values()
            if stored[: len(prefix)] == prefix
        ]
        return {"items": matches[offset : offset + limit]}

    monkeypatch.setattr(fake_store, "search_items", search_items)
    return fake_store


async def test_a_connection_round_trips_through_its_row() -> None:
    await make_user("alice")
    store = MCPConnectionStore("user", "alice")
    assert await store.get("linear") is None
    record = connection(
        "linear",
        transport="sse",
        enabled=False,
        allowed_tools=["search"],
        header_names=["Authorization"],
        oauth={"token_url": "https://auth.example/token", "client_id": "app", "scope": "read"},
        encrypted_headers="header-ciphertext",
        encrypted_client_secret="secret-ciphertext",
    )

    await store.put("linear", record)

    assert await store.get("linear") == record
    assert await store.list_all() == [record]

    await store.delete("linear")
    assert await store.get("linear") is None
    assert await store.list_all() == []
    await store.delete("linear")


async def test_one_name_in_two_scopes_or_two_owners_is_two_connections() -> None:
    await make_user("acme")
    await make_user("other")
    user = MCPConnectionStore("user", "acme")
    workspace = MCPConnectionStore("workspace", "acme")
    await user.put("linear", connection("linear", url="https://user.example/mcp"))
    await workspace.put("linear", connection("linear", url="https://workspace.example/mcp"))

    owned = await user.get("linear")
    shared = await workspace.get("linear")
    assert owned is not None and owned.url == "https://user.example/mcp"
    assert shared is not None and shared.url == "https://workspace.example/mcp"
    assert await MCPConnectionStore("user", "other").list_all() == []


async def test_the_owner_is_normalized() -> None:
    await make_user("Alice")
    store = MCPConnectionStore("user", " Alice ")
    assert store.owner == "alice"

    await store.put("linear", connection("linear"))

    assert await MCPConnectionStore("user", "ALICE").get("linear") is not None
    assert await stored_user_row("alice", "linear") is not None


async def test_a_login_with_no_user_row_reads_empty_and_cannot_save() -> None:
    """A session predating the ``users`` table has nothing to hang a connection off."""
    store = MCPConnectionStore("user", "ghost")

    assert await store.get("linear") is None
    assert await store.list_all() == []
    await store.delete("linear")
    with pytest.raises(ValueError, match="Sign in again"):
        await store.put("linear", connection("linear"))


async def test_deleting_a_user_deletes_their_connections() -> None:
    user_id = await make_user("alice")
    store = MCPConnectionStore("user", "alice")
    await store.put("linear", connection("linear"))
    assert await stored_user_names(user_id) == ["linear"]

    async with postgres.session() as session:
        await session.execute(delete(User).where(User.id == user_id))

    assert await stored_user_names(user_id) == []


async def test_list_all_is_sorted_and_skips_an_unreadable_row(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One row an older release wrote must not take a whole listing down."""
    store = MCPConnectionStore("workspace", "default")
    await store.put("charts", connection("charts"))
    await store.put("alerts", connection("alerts"))
    await store.put("broken", connection("broken", encrypted_headers="header-ciphertext"))
    async with postgres.session() as session:
        await session.execute(
            update(WorkspaceMCPConnectionRow)
            .where(WorkspaceMCPConnectionRow.name == "broken")
            .values(oauth={"token_url": "http://insecure.example/token", "client_id": "app"})
        )

    with caplog.at_level(logging.ERROR):
        records = await store.list_all()

    assert [record.name for record in records] == ["alerts", "charts"]
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "header-ciphertext" not in caplog.text


async def test_put_over_an_existing_name_keeps_created_at_and_replaces_the_rest() -> None:
    await make_user("alice")
    store = MCPConnectionStore("user", "alice")
    await store.put(
        "linear",
        connection("linear", allowed_tools=["search"], encrypted_headers="header-ciphertext"),
    )
    first = await stored_user_row("alice", "linear")
    assert first is not None

    replaced = connection("linear", enabled=False)
    await store.put("linear", replaced)

    assert await store.get("linear") == replaced
    again = await stored_user_row("alice", "linear")
    assert again is not None
    assert again.created_at == first.created_at
    assert again.allowed_tools == []
    assert again.encrypted_headers == ""


async def test_import_moves_every_scope_into_rows_and_empties_the_store(
    prefix_store: FakeStore,
) -> None:
    await make_user("alice")
    prefix_store.seed(
        [*USER_MCPS_NAMESPACE, "alice"], "linear", connection("linear").model_dump(mode="json")
    )
    prefix_store.seed(
        [*WORKSPACE_MCPS_NAMESPACE, "oss"], "docs", connection("docs").model_dump(mode="json")
    )
    prefix_store.seed(
        WORKSPACE_MCPS_NAMESPACE,
        "legacy",
        {"name": "legacy", "url": "https://legacy.example/mcp", "transport": "sse"},
    )

    assert await import_store_records() == 3

    user = await MCPConnectionStore("user", "alice").list_all()
    nested = await MCPConnectionStore("workspace", "oss").list_all()
    assert [record.name for record in user] == ["linear"]
    assert [record.name for record in nested] == ["docs"]
    # A flat record predates workspaces, so it is the default workspace's, and
    # predates the bookkeeping fields, which are stamped rather than rejected.
    legacy = await MCPConnectionStore("workspace", DEFAULT_WORKSPACE_SLUG).get("legacy")
    assert legacy is not None
    assert legacy.transport == "sse"
    assert legacy.revision and legacy.updated_at

    assert prefix_store.values([*USER_MCPS_NAMESPACE, "alice"]) == {}
    assert prefix_store.values([*WORKSPACE_MCPS_NAMESPACE, "oss"]) == {}
    assert prefix_store.values(WORKSPACE_MCPS_NAMESPACE) == {}
    assert await import_store_records() == 0


async def test_import_leaves_an_unreadable_record_in_the_store(
    prefix_store: FakeStore, caplog: pytest.LogCaptureFixture
) -> None:
    await make_user("alice")
    prefix_store.seed(
        [*USER_MCPS_NAMESPACE, "alice"], "linear", connection("linear").model_dump(mode="json")
    )
    prefix_store.seed(
        [*USER_MCPS_NAMESPACE, "alice"],
        "broken",
        {
            **connection("broken").model_dump(mode="json"),
            "encrypted_headers": {"Authorization": "test-secret"},
        },
    )

    with caplog.at_level(logging.ERROR):
        assert await import_store_records() == 1

    imported = await MCPConnectionStore("user", "alice").list_all()
    assert [record.name for record in imported] == ["linear"]
    assert sorted(prefix_store.values([*USER_MCPS_NAMESPACE, "alice"])) == ["broken"]
    assert "test-secret" not in caplog.text


async def test_import_leaves_a_record_whose_login_has_no_user(prefix_store: FakeStore) -> None:
    await make_user("alice")
    prefix_store.seed(
        [*USER_MCPS_NAMESPACE, "alice"], "linear", connection("linear").model_dump(mode="json")
    )
    prefix_store.seed(
        [*USER_MCPS_NAMESPACE, "ghost"], "docs", connection("docs").model_dump(mode="json")
    )

    assert await import_store_records() == 1

    imported = await MCPConnectionStore("user", "alice").list_all()
    assert [record.name for record in imported] == ["linear"]
    assert prefix_store.values([*USER_MCPS_NAMESPACE, "alice"]) == {}
    assert sorted(prefix_store.values([*USER_MCPS_NAMESPACE, "ghost"])) == ["docs"]


async def test_import_does_not_overwrite_a_row_it_already_has(prefix_store: FakeStore) -> None:
    store = MCPConnectionStore("workspace", "oss")
    kept = connection("docs", url="https://kept.example/mcp")
    await store.put("docs", kept)
    prefix_store.seed(
        [*WORKSPACE_MCPS_NAMESPACE, "oss"],
        "docs",
        connection("docs", url="https://stale.example/mcp").model_dump(mode="json"),
    )

    assert await import_store_records() == 0

    assert await store.get("docs") == kept
    # Consumed even though it was not copied, so a second run has nothing to do.
    assert prefix_store.values([*WORKSPACE_MCPS_NAMESPACE, "oss"]) == {}
