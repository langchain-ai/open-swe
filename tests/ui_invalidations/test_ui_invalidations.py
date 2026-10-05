import asyncio
import json
from contextlib import aclosing

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from agent.dashboard import repo_access
from agent.database import notifications, postgres
from agent.ui_invalidations import hub, outbox, topics
from agent.ui_invalidations.routes import _stream
from agent.ui_invalidations.topics import WORKSPACES
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG
from agent.workspaces.store import WORKSPACES as WORKSPACE_STORE


def _parse(frame: str) -> tuple[str, dict[str, object]]:
    event, data = frame.strip().split("\n")
    return event.removeprefix("event: "), json.loads(data.removeprefix("data: "))


async def test_a_reconnecting_reader_is_told_only_what_committed_within_its_window(
    registry_db: None,
) -> None:
    async with postgres.transaction() as conn:
        await outbox.invalidate(conn, WORKSPACES)
    try:
        async with postgres.transaction() as conn:
            await outbox.invalidate(conn, "workspaces/rolled-back")
            raise RuntimeError("abort")
    except RuntimeError:
        pass

    assert await outbox.invalidated_since({WORKSPACES: 5, "workspaces/rolled-back": 5}) == {
        WORKSPACES
    }

    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE ui_invalidation SET created_at = created_at - interval '10 minutes'")
        )
    assert await outbox.invalidated_since({WORKSPACES: 60}) == set()
    assert await outbox.invalidated_since({WORKSPACES: 3600}) == {WORKSPACES}

    assert await outbox.prune() == 0
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE ui_invalidation SET created_at = created_at - interval '2 days'")
        )
    assert await outbox.prune() == 1


async def test_a_workspace_write_reaches_an_open_stream(registry_db: None) -> None:
    await hub.start()
    notifications.start()
    try:
        async with asyncio.timeout(10):
            while notifications._CONNECTION is None:
                await asyncio.sleep(0.05)
            async with aclosing(_stream({WORKSPACES: 0}, [])) as frames:
                event, hello = _parse(await anext(frames))
                assert (event, hello["invalidated"], hello["denied"]) == ("hello", [], [])

                await WORKSPACE_STORE.mark_refresh_builder(DEFAULT_WORKSPACE_SLUG, None)

                assert _parse(await anext(frames)) == ("invalidated", {"topics": [WORKSPACES]})
    finally:
        await hub.stop()
        await notifications.stop()


async def test_a_pull_request_topic_is_heard_only_by_readers_of_its_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def require_access(_login: str, full_name: str) -> str:
        if full_name.lower() != "lc/repo":
            raise HTTPException(404, "repository not found")
        return "token"

    monkeypatch.setattr(repo_access, "require_repo_access_for_user", require_access)
    session = {"sub": "ada"}

    assert await topics.may_hear(session, topics.pull_request_topic("LC", "Repo", 7))
    assert not await topics.may_hear(session, topics.pull_request_topic("lc", "private", 7))
    assert not await topics.may_hear(session, "pull-request/lc/repo")
