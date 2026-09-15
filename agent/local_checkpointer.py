"""SQLite checkpointer for the desktop app's bundled ``langgraph dev`` server.

``langgraph dev`` keeps checkpoints in memory and pickles them to
``.langgraph_api/`` every ten seconds. When the desktop app is quit or killed
before that flush, or the pickle fails to serialize, users lose their threads.
This module is wired through ``checkpointer.path`` in ``langgraph.desktop.json``
so every checkpoint commits to SQLite as it is written.

Existing pickled checkpoints are imported into the database the first time it
is created, so threads started before the switch keep their history.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)

DB_PATH_ENV = "OPEN_SWE_LOCAL_CHECKPOINT_DB"
STATE_DIR = Path(".langgraph_api")
DEFAULT_DB_NAME = "checkpoints.sqlite"
IMPORT_MARKER_SUFFIX = ".imported-pickles"
# Seconds to wait on a locked database. The dev server opens one connection per
# event-loop thread, so writers can briefly contend on the same file.
BUSY_TIMEOUT_SECONDS = 5.0


def checkpoint_db_path() -> Path:
    """Resolve the checkpoint database path.

    The desktop app sets ``OPEN_SWE_LOCAL_CHECKPOINT_DB`` to a file under its
    local data directory. Without it the database lives next to the dev
    server's other state in ``.langgraph_api`` under the working directory.
    """
    configured = os.environ.get(DB_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return STATE_DIR / DEFAULT_DB_NAME


@asynccontextmanager
async def create_checkpointer() -> AsyncIterator[AsyncSqliteSaver]:
    """Yield an ``AsyncSqliteSaver`` backed by the local checkpoint database."""
    db_path = checkpoint_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path), timeout=BUSY_TIMEOUT_SECONDS) as conn:
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        await import_pickled_checkpoints(saver, db_path)
        yield saver


def _claim_import(marker: Path) -> bool:
    """Atomically claim the one-time import; only the first caller wins."""
    try:
        with marker.open("x"):
            return True
    except FileExistsError:
        return False


async def import_pickled_checkpoints(
    saver: BaseCheckpointSaver[str], db_path: Path, pickle_dir: Path = STATE_DIR
) -> int:
    """Copy checkpoints from ``langgraph dev``'s pickle files into ``saver`` once.

    Returns the number of checkpoints imported. Runs only when pickle files
    exist and no earlier import has been recorded next to the database. The
    pickle files are left in place; the import never blocks server startup.
    """
    marker = db_path.with_name(db_path.name + IMPORT_MARKER_SUFFIX)
    if not any(pickle_dir.glob(".langgraph_checkpoint.*.pckl")):
        return 0
    if not _claim_import(marker):
        return 0
    try:
        imported = await _copy_pickled_checkpoints(saver)
    except Exception:
        marker.unlink(missing_ok=True)
        logger.exception("Could not import pickled checkpoints into SQLite")
        return 0
    logger.info("Imported pickled checkpoints into SQLite", extra={"count": imported})
    return imported


async def _copy_pickled_checkpoints(saver: BaseCheckpointSaver[str]) -> int:
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
