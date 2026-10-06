import asyncio
import json
from contextlib import aclosing

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from agent.dashboard import repo_access
from agent.database import postgres
from agent.database.notifications import LISTENER
from agent.ui_invalidations import Topic, outbox
from agent.ui_invalidations.hub import HUB
from agent.ui_invalidations.routes import _stream
from agent.ui_invalidations.topics import BaseTopic
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES


def _parse(frame: str) -> tuple[str, dict[str, object]]:
    event, data = frame.strip().split("\n")
    return event.removeprefix("event: "), json.loads(data.removeprefix("data: "))


async def test_a_reconnecting_reader_is_told_only_what_committed_within_its_window(
    registry_db: None,
) -> None:
    workspaces = Topic.WORKSPACES.name
    async with postgres.transaction() as conn:
        await Topic.WORKSPACES.invalidate(conn)
    try:
        async with postgres.transaction() as conn:
            await Topic.REVIEW_STYLES.invalidate(conn)
            raise RuntimeError("abort")
    except RuntimeError:
        pass

    assert await outbox.invalidated_since({workspaces: 5, Topic.REVIEW_STYLES.name: 5}) == {
        workspaces
    }

    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE ui_invalidation SET created_at = created_at - interval '10 minutes'")
        )
    assert await outbox.invalidated_since({workspaces: 60}) == set()
    assert await outbox.invalidated_since({workspaces: 3600}) == {workspaces}

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
            async with aclosing(_stream({Topic.WORKSPACES.name: 0}, [])) as frames:
                event, hello = _parse(await anext(frames))
                assert (event, hello["invalidated"], hello["denied"]) == ("hello", [], [])

                await WORKSPACES.mark_refresh_builder(DEFAULT_WORKSPACE_SLUG, None)

                assert _parse(await anext(frames)) == (
                    "invalidated",
                    {"topics": [Topic.WORKSPACES.name]},
                )
    finally:
        await HUB.stop()
        await LISTENER.stop()


async def test_a_pull_request_topic_is_heard_only_by_readers_of_its_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def require_access(_login: str, full_name: str) -> str:
        if full_name.lower() != "lc/repo":
            raise HTTPException(404, "repository not found")
        return "token"

    monkeypatch.setattr(repo_access, "require_repo_access_for_user", require_access)
    session = {"sub": "ada"}

    assert await BaseTopic.may_hear(session, Topic.PULL_REQUESTS.keyed("lc/repo/7"))
    assert not await BaseTopic.may_hear(session, Topic.PULL_REQUESTS.keyed("lc/private/7"))
    assert not await BaseTopic.may_hear(session, Topic.PULL_REQUESTS.keyed("lc/repo"))
