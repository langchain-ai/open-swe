"""Short links to sandbox files, against a real migrated schema."""

from datetime import UTC, datetime, timedelta

from openswe.sandboxes.file_links import SandboxFileLink, create_link, get_link
from openswe.transcript.engine import Command, append, delete_transcript
from openswe.transcript.events import ThreadCreated


async def _create_thread(thread_id: str) -> None:
    created = ThreadCreated(title="A thread", source="dashboard", owner_login="test-user")
    await append(
        thread_id,
        [Command(command_id=f"thread:{thread_id}:created", event=created, actor_kind="user")],
    )


async def _link(
    thread_id: str = "thread-1", expires_in_seconds: int | None = None
) -> SandboxFileLink | None:
    return await create_link(
        thread_id=thread_id,
        sandbox_id="sandbox-1",
        path="/workspace/project/shot.png",
        content_type="image/png",
        content_disposition="inline",
        expires_in_seconds=expires_in_seconds,
    )


async def test_a_link_stores_its_target_under_a_short_random_id(registry_db: None) -> None:
    await _create_thread("thread-1")

    link = await _link()

    assert link is not None
    assert len(link.id) == 22
    stored = await get_link(link.id)
    assert stored is not None
    assert (
        stored.thread_id,
        stored.sandbox_id,
        stored.path,
        stored.content_type,
        stored.content_disposition,
        stored.expires_at,
    ) == ("thread-1", "sandbox-1", "/workspace/project/shot.png", "image/png", "inline", None)


async def test_a_link_records_when_it_expires(registry_db: None) -> None:
    await _create_thread("thread-1")
    before = datetime.now(UTC)

    link = await _link(expires_in_seconds=600)

    assert link is not None
    stored = await get_link(link.id)
    assert stored is not None and stored.expires_at is not None
    assert (
        before + timedelta(seconds=600)
        <= stored.expires_at
        <= datetime.now(UTC) + timedelta(seconds=600)
    )


async def test_a_thread_without_a_transcript_row_gets_no_link(registry_db: None) -> None:
    assert await _link("legacy-thread") is None


async def test_deleting_the_thread_deletes_its_links(registry_db: None) -> None:
    await _create_thread("thread-1")
    link = await _link()
    assert link is not None

    assert await delete_transcript("thread-1")

    assert await get_link(link.id) is None


async def test_an_unknown_id_has_no_link(registry_db: None) -> None:
    assert await get_link("no-such-link") is None
