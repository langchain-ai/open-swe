"""SQLite checkpointer for the desktop app's bundled ``langgraph dev`` server.

The dev server keeps checkpoints in memory and pickles them every ten seconds,
so quitting the app loses recent thread history. ``langgraph.desktop.json``
wires this module through ``checkpointer.path`` so each checkpoint commits to
SQLite as it is written.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)

DB_PATH_ENV = "OPEN_SWE_LOCAL_CHECKPOINT_DB"
STATE_DIR = Path(".langgraph_api")


@asynccontextmanager
async def create_checkpointer() -> AsyncIterator[AsyncSqliteSaver]:
    db_path = Path(os.environ.get(DB_PATH_ENV) or STATE_DIR / "checkpoints.sqlite")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # The dev server opens one connection per event-loop thread, so writers can
    # briefly contend on the same file.
    async with aiosqlite.connect(str(db_path), timeout=5.0) as conn:
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        await _import_pickled_checkpoints(saver, db_path)
        yield saver


async def _import_pickled_checkpoints(saver: AsyncSqliteSaver, db_path: Path) -> None:
    """Copy ``langgraph dev``'s pickled checkpoints into ``saver`` once.

    A marker file next to the database claims the import atomically. The pickle
    files are left in place and a failed import never blocks startup.
    """
    if not any(STATE_DIR.glob(".langgraph_checkpoint.*.pckl")):
        return
    marker = db_path.with_name(db_path.name + ".imported-pickles")
    try:
        marker.touch(exist_ok=False)
    except FileExistsError:
        return
    try:
        imported = await _copy_pickled_checkpoints(saver)
    except Exception:
        marker.unlink(missing_ok=True)
        logger.exception("Could not import pickled checkpoints into SQLite")
        return
    logger.info("Imported pickled checkpoints into SQLite", extra={"count": imported})


async def _copy_pickled_checkpoints(saver: AsyncSqliteSaver) -> int:
    # The in-memory saver that wrote the pickles is the one that can read
    # them back; it loads ``.langgraph_api/.langgraph_checkpoint.*.pckl``
    # from the working directory when constructed.
    from langgraph_runtime_inmem.checkpoint import InMemorySaver

    source = InMemorySaver()
    imported = 0
    async for item in source.alist(None):
        configurable = item.config["configurable"]
        parent_config = item.parent_config or {
            "configurable": {
                "thread_id": configurable["thread_id"],
                "checkpoint_ns": configurable.get("checkpoint_ns", ""),
            }
        }
        await saver.aput(
            parent_config, item.checkpoint, item.metadata or {}, item.checkpoint["channel_versions"]
        )
        writes_by_task: dict[str, list[tuple[str, object]]] = {}
        for task_id, channel, value in item.pending_writes or []:
            writes_by_task.setdefault(task_id, []).append((channel, value))
        for task_id, writes in writes_by_task.items():
            await saver.aput_writes(item.config, writes, task_id)
        imported += 1
    return imported
