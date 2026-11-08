from uuid import UUID

import pytest

from openswe.database import postgres
from openswe.input_messages import person_introduction
from openswe.memories.store import MEMORIES, MemoryRow, MemoryTrigger, Scope
from openswe.users import User
from openswe.utils.authorship import CollaboratorIdentity, ThreadParticipant
from openswe.workspaces.rows import WorkspaceRow


@pytest.fixture(autouse=True)
def person_key_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-secret")


async def test_every_memory_reaches_the_curator_but_only_personal_ones_apply_at_once(
    registry_db: None,
) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person = User(display_name="Ada")
    async with postgres.session() as session:
        session.add_all([workspace, person])
    # Saved in a private thread before every memory reached the curator.
    async with postgres.session() as session:
        session.add(
            MemoryRow(
                scope="personal",
                body="kept quiet",
                workspace_id=workspace.id,
                from_private_thread=True,
                status="held",
                created_by=person.id,
            )
        )

    async def save(body: str, *, scope: Scope, private: bool) -> None:
        await MEMORIES.save(
            scope=scope,
            body=body,
            person=person.id,
            workspace=workspace.slug,
            thread_id=f"thread-{body}",
            from_private_thread=private,
        )

    await save("my own habit", scope="personal", private=True)
    await save("learned with others", scope="workspace", private=False)

    held = await MEMORIES.for_curator(workspace.slug, "held")
    assert [(memory.body, memory.thread_id) for memory in held] == [(None, None)]
    assert not await MEMORIES.decide(workspace.slug, held[0].id, "adopted", "")
    pending = await MEMORIES.for_curator(workspace.slug, "pending")
    assert {(memory.body, memory.thread_id) for memory in pending} == {
        ("my own habit", None),
        ("learned with others", "thread-learned with others"),
    }
    assert len({memory.person for memory in [*held, *pending]}) == 1
    # One from a thread others write in waits for the curator.
    assert {memory.body for memory in await MEMORIES.personal(person.id)} == {
        "kept quiet",
        "my own habit",
    }


async def test_a_superseded_memory_leaves_only_those_who_read_the_workspace_prompt(
    registry_db: None,
) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person = User(display_name="Ada")
    async with postgres.session() as session:
        session.add_all([workspace, person])
    row = await MEMORIES.save(
        scope="personal",
        body="include the link with a confirmation",
        person=person.id,
        workspace=workspace.slug,
        thread_id=None,
        from_private_thread=False,
    )
    assert await MEMORIES.decide(workspace.slug, row.id, "adopted", "")
    assert [m.id for m in await MEMORIES.personal(person.id, curated_in=workspace.slug)] == [row.id]
    assert await MEMORIES.decide(workspace.slug, row.id, "superseded", "")

    # Only runs that read that workspace's curated prompt drop it; elsewhere it stays.
    assert await MEMORIES.personal(person.id, curated_in=workspace.slug) == []
    for elsewhere in (None, "billing"):
        assert [m.id for m in await MEMORIES.personal(person.id, curated_in=elsewhere)] == [row.id]


async def test_taking_a_memory_back_asks_the_curator_unless_it_never_saw_it(
    registry_db: None,
) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person, other = User(display_name="Ada"), User(display_name="Grace")
    held = MemoryRow(
        scope="personal",
        body="held",
        workspace_id=workspace.id,
        from_private_thread=True,
        status="held",
        created_by=person.id,
    )
    async with postgres.session() as session:
        session.add_all([workspace, person, other])
    async with postgres.session() as session:
        session.add(held)

    async def save(body: str, *, scope: Scope) -> UUID:
        row = await MEMORIES.save(
            scope=scope,
            body=body,
            person=person.id,
            workspace=workspace.slug,
            thread_id=None,
            from_private_thread=False,
        )
        return row.id

    adopted = await save("adopted", scope="workspace")
    habit = await save("my habit", scope="personal")
    assert await MEMORIES.decide(workspace.slug, adopted, "adopted", "")
    assert await MEMORIES.decide(workspace.slug, habit, "adopted", "")

    assert await MEMORIES.withdraw(adopted, other.id) is None
    assert await MEMORIES.withdraw(adopted, person.id) == "requested"
    assert await MEMORIES.withdraw(held.id, person.id) == "deleted"
    assert await MEMORIES.withdraw(habit, person.id) == "requested"
    # Taking it back stops a personal memory at once, and the curator still hears of it.
    assert await MEMORIES.personal(person.id) == []
    pending = await MEMORIES.for_curator(workspace.slug, "pending")
    assert {(memory.id, memory.withdrawal_requested) for memory in pending} == {
        (adopted, True),
        (habit, True),
    }
    assert await MEMORIES.for_curator(workspace.slug, "held") == []


async def test_a_triggered_memory_leaves_context_until_recalled(registry_db: None) -> None:
    workspace = WorkspaceRow(slug="platform", name="Platform")
    person = User(display_name="Ada")
    async with postgres.session() as session:
        session.add_all([workspace, person])

    async def save(body: str, *, private: bool = False) -> UUID:
        row = await MEMORIES.save(
            scope="personal",
            body=body,
            person=person.id,
            workspace=workspace.slug,
            thread_id=None,
            from_private_thread=private,
        )
        return row.id

    broad, api, deploy = await save("broad"), await save("api only"), await save("deploy")
    repo, web = await save("whole repo"), await save("web only")
    await MEMORIES.set_trigger(workspace.slug, "repo/org/mono/api", "Working in api.")
    await MEMORIES.set_trigger(workspace.slug, "repo/org/mono/web", "Working in web.")
    await MEMORIES.set_trigger(workspace.slug, "repo/org/mono", "Working in org/mono.")
    await MEMORIES.set_trigger(workspace.slug, "task/deploy", "Deploying.")
    for slug, memory in (
        ("repo/org/mono/api", api),
        ("repo/org/mono/api", deploy),
        ("repo/org/mono/web", web),
        ("repo/org/mono", repo),
        ("task/deploy", deploy),
    ):
        assert await MEMORIES.link_trigger(workspace.slug, slug, memory, linked=True)
    assert not await MEMORIES.link_trigger(workspace.slug, "repo/org/other", broad, linked=True)
    shared = await MEMORIES.save(
        scope="workspace",
        body="shared",
        person=person.id,
        workspace=workspace.slug,
        thread_id=None,
        from_private_thread=False,
    )
    assert not await MEMORIES.link_trigger(workspace.slug, "task/deploy", shared.id, linked=True)
    assert await MEMORIES.link_trigger(workspace.slug, "task/deploy", shared.id, linked=False)

    assert [m.id for m in await MEMORIES.personal(person.id)] == [broad]
    assert [t.line for t in await MEMORIES.triggers(person.id)] == [
        "repo/org/mono: Working in org/mono.",
        "repo/org/mono/api: Working in api.",
        "repo/org/mono/web: Working in web.",
        "task/deploy: Deploying.",
    ]
    # A path recalls what is stored there and above it, never what sits beside or below it.
    assert [m.id for m in await MEMORIES.recall(person.id, "repo/org/mono/api")] == [
        repo,
        deploy,
        api,
    ]
    assert [m.id for m in await MEMORIES.recall(person.id, "repo/org/mono/api/db")] == [
        repo,
        deploy,
        api,
    ]
    assert [m.id for m in await MEMORIES.recall(person.id, "repo/org/mono")] == [repo]
    assert [m.id for m in await MEMORIES.recall(person.id, "repo/org/mon")] == []
    assert [m.id for m in await MEMORIES.recall(person.id, "task/deploy")] == [deploy]

    # Guidance at a path reaches every thread, most general path first.
    assert await MEMORIES.set_guidance(workspace.slug, "repo/org/mono/api", "Api line.")
    assert await MEMORIES.set_guidance(workspace.slug, "repo/org/mono", "Mono line.")
    assert not await MEMORIES.set_guidance(workspace.slug, "repo/org/none", "No trigger.")
    assert [t.path for t in await MEMORIES.shared_triggers(workspace.slug)] == [
        "repo/org/mono",
        "repo/org/mono/api",
    ]
    assert [
        g.guidance for g in await MEMORIES.recall_guidance(workspace.slug, "repo/org/mono/api")
    ] == [
        "Mono line.",
        "Api line.",
    ]
    assert [
        g.guidance for g in await MEMORIES.recall_guidance(workspace.slug, "repo/org/mono/web")
    ] == ["Mono line."]
    assert MemoryTrigger.path_of(" Repo:Org/Mono/ ") == "repo/org/mono"

    # A memory stays held back while any trigger still links it.
    assert await MEMORIES.delete_trigger(workspace.slug, "repo/org/mono/api")
    assert {m.id for m in await MEMORIES.personal(person.id)} == {broad, api}
    curated = await MEMORIES.for_curator(workspace.slug, "pending")
    assert {m.id: m.triggers for m in curated}[deploy] == ["task/deploy"]

    participant = ThreadParticipant(
        identity=CollaboratorIdentity("Ada", "Ada", "ada@example.com"),
        person_id=f"user:{person.id}",
        memories=tuple(m.line for m in await MEMORIES.personal(person.id)),
        memory_triggers=tuple(t.line for t in await MEMORIES.triggers(person.id)),
    )
    introduction = person_introduction(participant.as_person())["content"]
    assert f"broad (memory {broad})" in introduction
    assert "task/deploy: Deploying." in introduction
    assert "repo/org/mono/api" not in introduction
    assert "deploy (" not in introduction

    private = await save("private deployment", private=True)
    assert await MEMORIES.link_trigger(workspace.slug, "task/deploy", private, linked=True)
    public = {deploy}
    for i in range(50):
        memory = await save(f"deployment {i}")
        assert await MEMORIES.link_trigger(workspace.slug, "task/deploy", memory, linked=True)
        public.add(memory)
    assert {m.id for m in await MEMORIES.recall(person.id, "task/deploy")} == public
    assert {
        m.id for m in await MEMORIES.recall(person.id, "task/deploy", include_private=True)
    } == public | {private}
