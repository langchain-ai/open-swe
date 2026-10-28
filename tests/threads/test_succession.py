from collections.abc import Mapping

import pytest
from pydantic import JsonValue

from openswe.thread_ids import mda_successor_thread_id
from openswe.threads import index, succession

pytestmark = pytest.mark.usefixtures("registry_db")

THREAD = "00000000-0000-4000-8000-000000000001"


class _Threads:
    def __init__(self) -> None:
        self.created: list[tuple[str, Mapping[str, JsonValue]]] = []
        self.updated: list[tuple[str, Mapping[str, JsonValue]]] = []

    async def create(
        self, *, thread_id: str, metadata: Mapping[str, JsonValue], if_exists: str
    ) -> None:
        self.created.append((thread_id, metadata))
        await index.create(thread_id, runtime="mda", metadata=metadata, if_exists="do_nothing")

    async def update(self, thread_id: str, *, metadata: Mapping[str, JsonValue]) -> None:
        self.updated.append((thread_id, metadata))


class _Client:
    def __init__(self) -> None:
        self.threads = _Threads()


async def test_work_on_a_local_thread_moves_once_to_a_successor_with_its_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    monkeypatch.setattr(succession, "langgraph_client", lambda: client)
    await index.create(
        THREAD,
        runtime="local",
        metadata={"kind": "reviewer", "watch": True, "latest_run_id": "run-1"},
    )

    first = await succession.succeed(THREAD)
    second = await succession.succeed(THREAD)

    successor = mda_successor_thread_id(THREAD)
    assert first == second == successor
    [(created_id, metadata)] = client.threads.created
    assert created_id == successor
    assert metadata == {"kind": "reviewer", "watch": True}
    assert client.threads.updated == [(THREAD, {"succeeded_by": successor})]
    assert await index.current_thread_id(THREAD) == successor
