import asyncio
import json
from contextlib import aclosing

from sqlalchemy import text

from agent.database import postgres
from agent.database.notifications import LISTENER
from agent.ui_invalidations import Topic, outbox
from agent.ui_invalidations.hub import HUB
from agent.ui_invalidations.routes import _stream
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES


def _parse(frame: str) -> tuple[str, dict[str, object]]:
    event, data = frame.strip().split("\n")
    return event.removeprefix("event: "), json.loads(data.removeprefix("data: "))


async def test_a_reconnecting_reader_is_told_only_what_committed_within_its_window(
    registry_db: None,
) -> None:
    async with postgres.transaction() as conn:
        await Topic.WORKSPACES.invalidate(conn)
    try:
        async with postgres.transaction() as conn:
            await Topic.WORKSPACES.invalidate(conn, key="rolled-back")
            raise RuntimeError("abort")
    except RuntimeError:
        pass

    assert await outbox.invalidated_since({Topic.WORKSPACES: 5, "workspaces/rolled-back": 5}) == {
        Topic.WORKSPACES
    }

    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE ui_invalidation SET created_at = created_at - interval '10 minutes'")
        )
    assert await outbox.invalidated_since({Topic.WORKSPACES: 60}) == set()
    assert await outbox.invalidated_since({Topic.WORKSPACES: 3600}) == {Topic.WORKSPACES}

    assert await outbox.prune() == 0
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE ui_invalidation SET created_at = created_at - interval '2 days'")
        )
    assert await outbox.prune() == 1


async def test_a_workspace_write_reaches_an_open_stream(registry_db: None) -> None:
    await HUB.start()
    LISTENER.start()
    try:
        async with asyncio.timeout(10):
            while not LISTENER.connected:
                await asyncio.sleep(0.05)
            async with aclosing(_stream({Topic.WORKSPACES: 0}, [])) as frames:
                event, hello = _parse(await anext(frames))
                assert (event, hello["invalidated"], hello["denied"]) == ("hello", [], [])

                await WORKSPACES.mark_refresh_builder(DEFAULT_WORKSPACE_SLUG, None)

                assert _parse(await anext(frames)) == (
                    "invalidated",
                    {"topics": [Topic.WORKSPACES]},
                )
    finally:
        await HUB.stop()
        await LISTENER.stop()
