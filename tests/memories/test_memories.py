from uuid import UUID

import pytest

from openswe.database import postgres
from openswe.memories.store import MEMORIES, Author, Scope
from openswe.users import User, UserIdentity
from openswe.workspaces.rows import WorkspaceRow


async def test_the_curator_reads_only_shared_threads_and_who_saved_them(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ada")
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person = User(
        display_name="Ada Lovelace",
        identities=[
            UserIdentity(provider="github", external_id="101", login="ada"),
            UserIdentity(provider="slack", external_id="U101"),
        ],
    )
    async with postgres.session() as session:
        session.add_all([workspace, person])

    async def save(body: str, *, scope: Scope, private: bool) -> UUID:
        row = await MEMORIES.save(
            scope=scope,
            body=body,
            person=person.id,
            workspace=workspace.slug,
            thread_id=f"thread-{body}",
            from_private_thread=private,
        )
        return row.id

    kept_quiet = await save("kept quiet", scope="workspace", private=True)
    await save("my own habit", scope="personal", private=True)
    await save("learned in public", scope="workspace", private=False)

    pending = await MEMORIES.for_curator(workspace.slug, "pending")
    assert [(memory.body, memory.thread_id) for memory in pending] == [
        ("learned in public", "thread-learned in public")
    ]
    assert pending[0].author == Author(
        full_name="Ada Lovelace",
        github_login="ada",
        github_user_id="101",
        slack_user_id="U101",
        admin=True,
    )
    assert not await MEMORIES.decide(workspace.slug, kept_quiet, "adopted", "")
    # A personal memory applies to its person; until the curator processes them, so do
    # the workspace memories they saved in their own private threads, in that workspace
    # alone. One from a shared thread waits for the curator.
    assert [memory.body for memory in await MEMORIES.personal(person.id)] == ["my own habit"]
    unprocessed = await MEMORIES.personal(person.id, unprocessed_in=workspace.slug)
    assert {memory.body for memory in unprocessed} == {"kept quiet", "my own habit"}
    elsewhere = await MEMORIES.personal(person.id, unprocessed_in="billing")
    assert [memory.body for memory in elsewhere] == ["my own habit"]


async def test_taking_a_memory_back_asks_the_curator_unless_it_never_saw_it(
    registry_db: None,
) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person, other = User(display_name="Ada"), User(display_name="Grace")
    async with postgres.session() as session:
        session.add_all([workspace, person, other])

    async def save(body: str, *, scope: Scope, private: bool) -> UUID:
        row = await MEMORIES.save(
            scope=scope,
            body=body,
            person=person.id,
            workspace=workspace.slug,
            thread_id=None,
            from_private_thread=private,
        )
        return row.id

    adopted = await save("adopted", scope="workspace", private=False)
    held = await save("held", scope="workspace", private=True)
    habit = await save("my habit", scope="personal", private=True)
    assert await MEMORIES.decide(workspace.slug, adopted, "adopted", "")

    assert await MEMORIES.withdraw(adopted, other.id, "workspace") is None
    assert await MEMORIES.withdraw(adopted, person.id, "personal") is None
    assert await MEMORIES.withdraw(adopted, person.id, "workspace") == "requested"
    assert await MEMORIES.withdraw(held, person.id, "workspace") == "deleted"
    assert await MEMORIES.withdraw(habit, person.id, "personal") == "deleted"
    assert await MEMORIES.personal(person.id) == []
    pending = await MEMORIES.for_curator(workspace.slug, "pending")
    assert [(memory.id, memory.withdrawal_requested) for memory in pending] == [(adopted, True)]
