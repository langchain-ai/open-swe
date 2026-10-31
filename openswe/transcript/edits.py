"""Historical edit evidence for sidebar thread summaries."""

import logging
from collections.abc import Sequence

from sqlalchemy import ARRAY, Text, bindparam, text

from openswe.database import postgres

logger = logging.getLogger(__name__)

_EDITED_THREADS = text(
    """
    SELECT DISTINCT thread_id FROM thread_tool_call
    WHERE thread_id = ANY(:thread_ids)
      AND name IN ('write_file', 'edit_file', 'delete') AND status = 'completed'
    UNION
    SELECT thread_id FROM thread_turn_checkpoint
    WHERE thread_id = ANY(:thread_ids) AND files <> '[]'::jsonb
    """
).bindparams(bindparam("thread_ids", type_=ARRAY(Text)))


async def attach_edit_evidence(summaries: Sequence[dict[str, object]]) -> None:
    """Recover edits predating the durable thread flag in one batched read."""
    ids = [
        thread_id
        for summary in summaries
        if summary.get("source") == "slack"
        and not summary.get("hasEdits")
        and isinstance(thread_id := summary.get("id"), str)
    ]
    if not ids or not postgres.configured():
        return
    try:
        async with postgres.snapshot_transaction() as conn:
            edited = set((await conn.execute(_EDITED_THREADS, {"thread_ids": ids})).scalars())
    except Exception:
        logger.warning("Could not read thread edit evidence", exc_info=True)
        for summary in summaries:
            if summary.get("id") in ids:
                summary["hasEdits"] = None
        return
    for summary in summaries:
        if summary.get("id") in edited:
            summary["hasEdits"] = True
