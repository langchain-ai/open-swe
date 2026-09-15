from pathlib import Path

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata, empty_checkpoint

from agent.local_checkpointer import DB_PATH_ENV, create_checkpointer


def _thread_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _checkpoint(value: str, version: str) -> Checkpoint:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": [value]}
    checkpoint["channel_versions"] = {"messages": version}
    return checkpoint


_METADATA: CheckpointMetadata = {"source": "input", "step": 0, "parents": {}}


async def test_checkpoints_survive_reopening_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "state" / "checkpoints.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(db_path))

    async with create_checkpointer() as saver:
        await saver.aput(
            _thread_config("t1"), _checkpoint("hello", "1"), _METADATA, {"messages": "1"}
        )

    assert db_path.exists()
    async with create_checkpointer() as saver:
        stored = await saver.aget_tuple(_thread_config("t1"))
    assert stored is not None
    assert stored.checkpoint["channel_values"] == {"messages": ["hello"]}


async def test_pickled_dev_server_checkpoints_are_imported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(DB_PATH_ENV, raising=False)
    # The dev server's saver constructs langgraph_api's serializer, whose config
    # module needs the connection settings the langgraph CLI sets for `dev`.
    monkeypatch.setenv("REDIS_URI", "fake")
    monkeypatch.setenv("DATABASE_URI", ":memory:")
    monkeypatch.setenv("MIGRATIONS_PATH", "__inmem")
    from langgraph_runtime_inmem.checkpoint import InMemorySaver

    # Write checkpoints the way `langgraph dev` does: pickled under .langgraph_api.
    with InMemorySaver() as legacy:
        first = await legacy.aput(
            _thread_config("old"), _checkpoint("one", "1"), _METADATA, {"messages": "1"}
        )
        await legacy.aput_writes(first, [("messages", "pending")], "task-1")
        await legacy.aput(first, _checkpoint("two", "2"), _METADATA, {"messages": "2"})
    assert list((tmp_path / ".langgraph_api").glob(".langgraph_checkpoint.*.pckl"))

    async with create_checkpointer() as saver:
        history = [item async for item in saver.alist(_thread_config("old"))]
        assert [item.checkpoint["channel_values"]["messages"] for item in history] == [
            ["two"],
            ["one"],
        ]
        assert history[1].pending_writes == [("task-1", "messages", "pending")]
        assert history[0].parent_config is not None
        assert (
            history[0].parent_config["configurable"]["checkpoint_id"]
            == first["configurable"]["checkpoint_id"]
        )

        # Later work must not be clobbered by a repeat import on the next start.
        await saver.adelete_thread("old")

    async with create_checkpointer() as saver:
        assert await saver.aget_tuple(_thread_config("old")) is None
