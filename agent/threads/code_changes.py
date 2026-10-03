"""File-edit evidence for Slack threads, without loading recorded conversations."""

import asyncio
import logging
from collections.abc import Mapping, Sequence

from langgraph_sdk.client import LangGraphClient
from sqlalchemy import ARRAY, Text, bindparam, text

from agent.database import postgres
from agent.threads.summary import _thread_id, thread_source
from agent.utils.json_types import ThreadLike, as_thread_dict, thread_metadata

logger = logging.getLogger(__name__)

_EDIT_TOOLS = frozenset(
    {"edit_file", "write_file", "delete", "str_replace", "write", "edit", "patch", "apply_patch"}
)
_EDIT_CALLS = text(
    """
    SELECT DISTINCT thread_id FROM thread_tool_call
    WHERE thread_id = ANY(:thread_ids) AND name = ANY(:names)
    """
).bindparams(bindparam("thread_ids", type_=ARRAY(Text)), bindparam("names", type_=ARRAY(Text)))


def slack_thread_without_pr(metadata: Mapping[str, object]) -> bool:
    return (
        thread_source(metadata) == "slack"
        and not metadata.get("pr_number")
        and not metadata.get("pr_url")
        and not metadata.get("pull_requests")
    )


def _has_edit_call(value: object) -> bool:
    if isinstance(value, Mapping):
        calls = value.get("tool_calls")
        if isinstance(calls, list) and any(
            isinstance(call, Mapping)
            and isinstance(call.get("name"), str)
            and call.get("name") in _EDIT_TOOLS
            for call in calls
        ):
            return True
        return any(_has_edit_call(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_edit_call(item) for item in value)
    return False


async def with_code_changes(
    client: LangGraphClient, threads: Sequence[ThreadLike]
) -> list[ThreadLike]:
    """Annotate edit evidence; failed reads stay unknown so filtering fails open."""
    pending = {
        thread_id: thread_metadata(thread)
        for thread in threads
        if (thread_id := _thread_id(thread))
        and slack_thread_without_pr(thread_metadata(thread))
        and not isinstance(thread_metadata(thread).get("has_edits"), bool)
    }
    evidence: dict[str, bool] = {}
    recorded = [key for key, metadata in pending.items() if metadata.get("transcript") == "v2"]
    if recorded and postgres.configured():
        try:
            async with postgres.snapshot_transaction() as conn:
                edited = set(
                    (
                        await conn.execute(
                            _EDIT_CALLS, {"thread_ids": recorded, "names": list(_EDIT_TOOLS)}
                        )
                    )
                    .scalars()
                    .all()
                )
            evidence.update({key: key in edited for key in recorded})
        except Exception:
            logger.warning("Could not read thread edit evidence", exc_info=True)

    semaphore = asyncio.Semaphore(8)

    async def legacy_evidence(thread_id: str) -> None:
        try:
            async with semaphore:
                state = await client.threads.get_state(thread_id, subgraphs=True)
            evidence[thread_id] = _has_edit_call(state)
        except Exception:
            logger.warning(
                "Could not read legacy thread edit evidence",
                exc_info=True,
                extra={"thread_id": thread_id},
            )

    await asyncio.gather(
        *(
            legacy_evidence(key)
            for key, metadata in pending.items()
            if metadata.get("transcript") != "v2"
        )
    )
    return [
        {
            **as_thread_dict(thread),
            "metadata": {**thread_metadata(thread), "has_edits": evidence[thread_id]},
        }
        if (thread_id := _thread_id(thread)) in evidence
        else thread
        for thread in threads
    ]


def has_slack_code_changes(thread: ThreadLike) -> bool:
    metadata = thread_metadata(thread)
    return not slack_thread_without_pr(metadata) or metadata.get("has_edits") is not False
