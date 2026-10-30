from uuid import UUID

from openswe.database import postgres
from openswe.memories.store import MEMORIES, Scope
from openswe.users import User
from openswe.workspaces.rows import WorkspaceRow


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


async def test_withdrawing_asks_the_curator_unless_it_never_saw_the_memory(
    registry_db: None,
) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person, other = User(display_name="Ada"), User(display_name="Grace")
    async with postgres.session() as session:
        session.add_all([workspace, person, other])

    async def save(body: str, *, private: bool) -> UUID:
        row = await MEMORIES.save(
            scope="workspace",
            body=body,
            person=person.id,
            workspace=workspace.slug,
            thread_id=None,
            from_private_thread=private,
            shared=False,
        )
        return row.id

    adopted, held = await save("adopted", private=False), await save("held", private=True)
    assert await MEMORIES.decide(workspace.slug, adopted, "adopted", "")

    assert await MEMORIES.withdraw(adopted, other.id) is None
    assert await MEMORIES.withdraw(adopted, person.id) == "requested"
    assert await MEMORIES.withdraw(held, person.id) == "deleted"
    pending = await MEMORIES.for_curator(workspace.slug, "pending")
    assert [(memory.id, memory.withdrawal_requested) for memory in pending] == [(adopted, True)]
    assert await MEMORIES.for_curator(workspace.slug, "held") == []
