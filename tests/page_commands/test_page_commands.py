"""A page session's commands queue in order, run in order, and stay its owner's alone."""

import asyncio
import json
from uuid import uuid7

import pytest
from fastapi import HTTPException

from openswe.page_commands.offers import PageSession
from openswe.page_commands.records import PageCommand, PageCommandOutOfTurn, PageQueue
from openswe.tools.page_commands import READ_PAGE_QUEUE, PageTools
from openswe.ui_invalidations import Topic
from openswe.ui_invalidations.hub import HUB
from openswe.users import User


def _offered(owner: object) -> PageSession:
    return PageSession.offered(
        str(uuid7()),
        owner,  # type: ignore[arg-type]
        [
            {
                "name": "review_show_code",
                "description": "Show code",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ],
    )


async def test_commands_queue_and_run_in_order_for_their_owner_only(registry_db: None) -> None:
    alice = await User.sign_in("github", "1", login="test-user")
    bob = await User.sign_in("github", "2", login="trusted-user")
    page = _offered(alice.id)
    tools = PageTools(page, "thread-1")
    show = tools.tool("page_review_show_code")
    assert show is not None

    positions = [
        json.loads(await show.ainvoke({"path": path}))["position"] for path in ("a.py", "b.py")
    ]
    assert positions == [1, 2]
    queue = PageQueue(page.id, alice.id)
    assert await queue.waiting() == [
        PageCommand(position=1, tool="review_show_code", arguments={"path": "a.py"}),
        PageCommand(position=2, tool="review_show_code", arguments={"path": "b.py"}),
    ]
    # The same session id under someone else is a queue of its own.
    assert await PageQueue(page.id, bob.id).waiting() == []

    with pytest.raises(PageCommandOutOfTurn):
        await queue.record(2, {"shown": "b.py"}, by=alice.id)
    await queue.record(1, {"shown": "a.py"}, by=alice.id)
    with pytest.raises(PageCommandOutOfTurn):
        await queue.record(1, {"shown": "again"}, by=alice.id)

    # Another thread chatting from the same tab queues behind it.
    other = PageTools(page, "thread-2").tool("page_review_show_code")
    assert other is not None
    assert json.loads(await other.ainvoke({"path": "c.py"}))["position"] == 3

    reader = tools.tool(READ_PAGE_QUEUE)
    assert reader is not None
    # A read waiting for the second command wakes once the page records it.
    waiting = asyncio.create_task(reader.ainvoke({"since": 1, "wait_for": 2}))
    await asyncio.sleep(0.1)
    assert not waiting.done()
    await queue.record(2, {"shown": "b.py"}, by=alice.id)
    HUB.deliver([queue.topic])
    assert json.loads(await asyncio.wait_for(waiting, timeout=5)) == {
        "queued_through": 2,
        "executed_through": 2,
        "results": [{"position": 2, "tool": "review_show_code", "result": {"shown": "b.py"}}],
    }


async def test_only_the_owner_hears_their_page_sessions() -> None:
    owner, other = uuid7(), uuid7()
    session = {"sub": "alice", "user_id": str(owner)}
    keys = {f"{owner}/{uuid7()}", f"{other}/{uuid7()}", ""}
    assert await Topic.PAGE_COMMANDS.authorize(session, keys) == {
        key for key in keys if key.startswith(str(owner))
    }


@pytest.mark.parametrize("name", ["_x", "has.dot", "has space", "x" * 60])
def test_a_page_cannot_offer_a_name_the_prefix_cannot_keep_apart(name: str) -> None:
    with pytest.raises(HTTPException) as raised:
        PageSession.offered(str(uuid7()), uuid7(), [{"name": name, "description": "d"}])
    assert raised.value.status_code == 400
