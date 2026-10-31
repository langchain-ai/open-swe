from uuid import UUID

import pytest

from openswe.database import postgres
from openswe.engrams.store import ENGRAMS, Scope
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
        await ENGRAMS.save(
            scope=scope,
            body=body,
            person=person.id,
            workspace=workspace.slug,
            thread_id=f"thread-{body}",
            from_private_thread=private,
            shared=shared,
        )

    await save("my own habit", scope="personal", private=True)
    await save("shared on purpose", scope="personal", private=True, shared=True)
    await save("learned with others", scope="workspace", private=False)

    held = await ENGRAMS.for_curator(workspace.slug, "held")
    assert [(engram.body, engram.thread_id) for engram in held] == [(None, None)]
    assert not await ENGRAMS.decide(workspace.slug, held[0].id, "adopted", "")
    pending = await ENGRAMS.for_curator(workspace.slug, "pending")
    assert {(engram.body, engram.thread_id) for engram in pending} == {
        ("shared on purpose", None),
        ("learned with others", "thread-learned with others"),
    }
    assert len({engram.person for engram in [*held, *pending]}) == 1
    # A personal engram applies to its person whether or not the curator may read it; one
    # from a thread others write in waits for the curator.
    assert {engram.body for engram in await ENGRAMS.personal(person.id)} == {
        "my own habit",
        "shared on purpose",
    }


async def test_an_adopted_engram_moves_into_the_workspace_prompt(registry_db: None) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person = User(display_name="Ada")
    async with postgres.session() as session:
        session.add_all([workspace, person])
    row = await ENGRAMS.save(
        scope="personal",
        body="include the link with a confirmation",
        person=person.id,
        workspace=workspace.slug,
        thread_id=None,
        from_private_thread=False,
        shared=False,
    )
    assert await ENGRAMS.decide(workspace.slug, row.id, "adopted", "")

    # Only runs that read that workspace's curated prompt drop it.
    assert await ENGRAMS.personal(person.id, curated_in=workspace.slug) == []
    for elsewhere in (None, "billing"):
        assert [e.id for e in await ENGRAMS.personal(person.id, curated_in=elsewhere)] == [row.id]


async def test_taking_an_engram_back_asks_the_curator_unless_it_never_saw_it(
    registry_db: None,
) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person, other = User(display_name="Ada"), User(display_name="Grace")
    async with postgres.session() as session:
        session.add_all([workspace, person, other])

    async def save(body: str, *, scope: Scope, private: bool, shared: bool = False) -> UUID:
        row = await ENGRAMS.save(
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
    held = await save("held", scope="personal", private=True)
    habit = await save("my habit, shared", scope="personal", private=True, shared=True)
    assert await ENGRAMS.decide(workspace.slug, adopted, "adopted", "")
    assert await ENGRAMS.decide(workspace.slug, habit, "adopted", "")

    assert await ENGRAMS.withdraw(adopted, other.id) is None
    assert await ENGRAMS.withdraw(adopted, person.id) == "requested"
    assert await ENGRAMS.withdraw(held, person.id) == "deleted"
    assert await ENGRAMS.withdraw(habit, person.id) == "requested"
    # Taking it back stops a personal engram at once, and the curator still hears of it.
    assert await ENGRAMS.personal(person.id) == []
    pending = await ENGRAMS.for_curator(workspace.slug, "pending")
    assert {(engram.id, engram.withdrawal_requested) for engram in pending} == {
        (adopted, True),
        (habit, True),
    }
    assert await ENGRAMS.for_curator(workspace.slug, "held") == []
