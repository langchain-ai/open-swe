"""The subagents a thread spawned, as the sidebar lists them under the thread.

A subagent is a root-level ``task`` tool call; everything it did is recorded in
the same transcript under a namespace that ends in the call's id, so this only
has to surface the call itself. Nested subagents are left to the subagent view.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ARRAY, Text, bindparam, text

from agent.database import postgres

logger = logging.getLogger(__name__)

SubagentStatus = Literal["in_progress", "completed", "error"]
_TITLE_CAP = 200


class SubagentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    toolCallId: str
    title: str
    subagentType: str
    status: SubagentStatus
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


def _ms(value: datetime | None) -> int | None:
    return int(value.timestamp() * 1000) if value is not None else None


def subagent_title(description: object) -> str:
    """The first line of the task description, which is how the model names the job."""
    if not isinstance(description, str):
        return "Subagent"
    first_line = description.strip().split("\n", 1)[0].strip()
    if not first_line:
        return "Subagent"
    return first_line if len(first_line) <= _TITLE_CAP else first_line[: _TITLE_CAP - 1] + "…"


def _status(value: object) -> SubagentStatus:
    if value == "completed":
        return "completed"
    if value == "error":
        return "error"
    return "in_progress"


async def load_root_subagents(
    thread_ids: Sequence[str],
) -> dict[str, list[SubagentSummary]]:
    """Root-level subagents per thread, oldest first; threads without any are absent.

    Callers pass only threads the viewer may already read. Threads without a
    transcript (or no Postgres at all) simply have no subagents to list.
    """
    ids = [thread_id for thread_id in dict.fromkeys(thread_ids) if thread_id]
    if not ids or not postgres.configured():
        return {}
    by_thread: dict[str, list[SubagentSummary]] = {}
    async with postgres.snapshot_transaction() as conn:
        result = await conn.execute(_ROOT_TASK_CALLS, {"thread_ids": ids})
        for row in result.mappings():
            raw_input = row["input"]
            task_input = raw_input if isinstance(raw_input, dict) else {}
            started_at = _ms(row["started_at"])
            if started_at is None:
                continue
            subagent_type = task_input.get("subagent_type")
            by_thread.setdefault(row["thread_id"], []).append(
                SubagentSummary(
                    toolCallId=row["tool_call_id"],
                    title=subagent_title(task_input.get("description")),
                    subagentType=(
                        subagent_type
                        if isinstance(subagent_type, str) and subagent_type
                        else "subagent"
                    ),
                    status=_status(row["status"]),
                    startedAt=started_at,
                    endedAt=_ms(row["ended_at"]),
                )
            )
    return by_thread


async def attach_subagents(summaries: Sequence[dict[str, object]]) -> None:
    """Set ``summaries[i]["subagents"]`` from one batched read; a failed read leaves them empty."""
    for summary in summaries:
        summary.setdefault("subagents", [])
    ids = [thread_id for summary in summaries if isinstance(thread_id := summary.get("id"), str)]
    if not ids:
        return
    try:
        by_thread = await load_root_subagents(ids)
    except Exception:
        logger.warning(
            "Subagent listing failed; threads render without subagents",
            exc_info=True,
            extra={"thread_count": len(ids)},
        )
        return
    for summary in summaries:
        thread_id = summary.get("id")
        if isinstance(thread_id, str) and thread_id in by_thread:
            summary["subagents"] = [
                subagent.model_dump(mode="json") for subagent in by_thread[thread_id]
            ]
