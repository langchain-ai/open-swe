from uuid import UUID

import pytest

from openswe.database import postgres
from openswe.memories.store import MEMORIES, Scope
from openswe.users import User
from openswe.workspaces.rows import WorkspaceRow


@pytest.fixture(autouse=True)
def person_key_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-secret")


async def test_the_curator_never_reads_what_a_private_thread_kept_to_itself(
    registry_db: None,
) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person = User(display_name="Ada")
    async with postgres.session() as session:
        session.add_all([workspace, person])

    async def save(body: str, *, scope: Scope, private: bool, shared: bool = False) -> None:
        await MEMORIES.save(
            scope=scope,
            body=body,
            person=person.id,
            workspace=workspace.slug,
            thread_id=f"thread-{body}",
            from_private_thread=private,
            shared=shared,
        )

    await save("kept quiet", scope="workspace", private=True)
    await save("my own habit", scope="personal", private=True)
    await save("shared on purpose", scope="workspace", private=True, shared=True)
    await save("learned in public", scope="workspace", private=False)

    held = await MEMORIES.for_curator(workspace.slug, "held")
    assert {(memory.scope, memory.body, memory.thread_id) for memory in held} == {
        ("workspace", None, None),
        ("personal", None, None),
    }
    assert not await MEMORIES.decide(workspace.slug, held[0].id, "adopted", "")
    pending = await MEMORIES.for_curator(workspace.slug, "pending")
    assert {(memory.body, memory.thread_id) for memory in pending} == {
        ("shared on purpose", None),
        ("learned in public", "thread-learned in public"),
    }
    assert len({memory.person for memory in [*held, *pending]}) == 1
    # A personal memory applies to its person whether or not the curator may read it.
    assert [memory.body for memory in await MEMORIES.personal(person.id)] == ["my own habit"]
    # Until the curator processes them, the workspace memories they saved in their own
    # private threads apply to them too, but only in that workspace; one from a shared
    # thread waits for the curator.
    unprocessed = await MEMORIES.personal(person.id, unprocessed_in=workspace.slug)
    assert {memory.body for memory in unprocessed} == {
        "kept quiet",
        "my own habit",
        "shared on purpose",
    }
    elsewhere = await MEMORIES.personal(person.id, unprocessed_in="billing")
    assert [memory.body for memory in elsewhere] == ["my own habit"]
    assert pending[0].body == "shared on purpose"
    assert await MEMORIES.decide(workspace.slug, pending[0].id, "adopted", "")
    still = await MEMORIES.personal(person.id, unprocessed_in=workspace.slug)
    assert pending[0].id not in {memory.id for memory in still}


async def test_taking_a_memory_back_asks_the_curator_unless_it_never_saw_it(
    registry_db: None,
) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person, other = User(display_name="Ada"), User(display_name="Grace")
    async with postgres.session() as session:
        session.add_all([workspace, person, other])

    async def save(body: str, *, scope: Scope, private: bool, shared: bool = False) -> UUID:
        row = await MEMORIES.save(
            scope=scope,
            body=body,
            person=person.id,
            workspace=workspace.slug,
            thread_id=None,
            from_private_thread=private,
            shared=shared,
        )
        return row.id

    adopted = await save("adopted", scope="workspace", private=False)
    held = await save("held", scope="workspace", private=True)
    habit = await save("my habit, shared", scope="personal", private=True, shared=True)
    assert await MEMORIES.decide(workspace.slug, adopted, "adopted", "")
    assert await MEMORIES.decide(workspace.slug, habit, "adopted", "")

    assert await MEMORIES.withdraw(adopted, other.id, "workspace") is None
    assert await MEMORIES.withdraw(adopted, person.id, "personal") is None
    assert await MEMORIES.withdraw(adopted, person.id, "workspace") == "requested"
    assert await MEMORIES.withdraw(held, person.id, "workspace") == "deleted"
    assert await MEMORIES.withdraw(habit, person.id, "personal") == "requested"
    # Taking it back stops a personal memory at once, and the curator still hears of it.
    assert await MEMORIES.personal(person.id) == []
    pending = await MEMORIES.for_curator(workspace.slug, "pending")
    assert {(memory.id, memory.withdrawal_requested) for memory in pending} == {
        (adopted, True),
        (habit, True),
    }
    assert await MEMORIES.for_curator(workspace.slug, "held") == []
