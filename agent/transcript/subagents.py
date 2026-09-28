"""The subagents a thread spawned, as the sidebar lists them under the thread.

A subagent is a root-level ``task`` tool call; everything it did is recorded in
the same transcript under a namespace that ends in the call's id, so this only
has to surface the call itself. Nested subagents are left to the subagent view.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Literal, TypedDict

from sqlalchemy import ARRAY, Text, bindparam, text

from agent.database import postgres

logger = logging.getLogger(__name__)

_TITLE_CAP = 200


class SubagentSummary(TypedDict):
    toolCallId: str
    title: str
    subagentType: str
    status: Literal["in_progress", "completed", "error"]
    startedAt: int
    endedAt: int | None


_ROOT_TASK_CALLS = text(
    """
    SELECT thread_id, tool_call_id, input, status, started_at, ended_at
    FROM thread_tool_call
    WHERE thread_id = ANY(:thread_ids) AND name = 'task' AND namespace = '{}'
    ORDER BY thread_id, started_at, tool_call_id
    """
).bindparams(bindparam("thread_ids", type_=ARRAY(Text)))


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _title(description: object) -> str:
    """The first line of the task description, which is how the model names the job."""
    first_line = (
        description.strip().split("\n", 1)[0].strip() if isinstance(description, str) else ""
    )
    if not first_line:
        return "Subagent"
    return first_line if len(first_line) <= _TITLE_CAP else first_line[: _TITLE_CAP - 1] + "…"


async def attach_subagents(summaries: Sequence[dict[str, object]]) -> None:
    """Set each summary's ``subagents`` (root-level, oldest first) from one batched read.

    Callers pass only threads the viewer may already read. Threads without a
    transcript, no Postgres at all, or a failed read leave the list empty so
    the thread list still loads.
    """
    for summary in summaries:
        summary["subagents"] = []
    ids = [thread_id for summary in summaries if isinstance(thread_id := summary.get("id"), str)]
    if not ids or not postgres.configured():
        return
    try:
        async with postgres.snapshot_transaction() as conn:
            rows = (await conn.execute(_ROOT_TASK_CALLS, {"thread_ids": ids})).mappings().all()
    except Exception:
        logger.warning(
            "Subagent listing failed; threads render without subagents",
            exc_info=True,
            extra={"thread_count": len(ids)},
        )
        return
    by_thread: dict[str, list[SubagentSummary]] = {}
    for row in rows:
        raw_input = row["input"]
        task_input = raw_input if isinstance(raw_input, dict) else {}
        subagent_type = task_input.get("subagent_type")
        by_thread.setdefault(row["thread_id"], []).append(
            {
                "toolCallId": row["tool_call_id"],
                "title": _title(task_input.get("description")),
                "subagentType": (
                    subagent_type
                    if isinstance(subagent_type, str) and subagent_type
                    else "subagent"
                ),
                "status": row["status"],
                "startedAt": _ms(row["started_at"]),
                "endedAt": _ms(row["ended_at"]) if row["ended_at"] is not None else None,
            }
        )
    for summary in summaries:
        thread_id = summary.get("id")
        if isinstance(thread_id, str) and thread_id in by_thread:
            summary["subagents"] = by_thread[thread_id]
