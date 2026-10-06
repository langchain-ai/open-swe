from collections.abc import Sequence

from sqlalchemy import ARRAY, Text, bindparam, text

from agent.database import postgres


async def record_guest_thread(host_thread_id: str, thread_id: str) -> None:
    async with postgres.transaction() as conn:
        await conn.execute(
            text("""
                INSERT INTO responses_guest_thread (host_thread_id, thread_id)
                VALUES (:host, :thread) ON CONFLICT (thread_id) DO NOTHING
            """),
            {"host": host_thread_id, "thread": thread_id},
        )


async def guest_thread_ids(host_ids: Sequence[str]) -> dict[str, list[str]]:
    if not host_ids or not postgres.configured():
        return {}
    query = text("""
        SELECT host_thread_id, thread_id FROM responses_guest_thread
        WHERE host_thread_id = ANY(:hosts) ORDER BY thread_id
    """).bindparams(bindparam("hosts", type_=ARRAY(Text)))
    async with postgres.snapshot_transaction() as conn:
        rows = (await conn.execute(query, {"hosts": list(host_ids)})).mappings().all()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row["host_thread_id"], []).append(row["thread_id"])
    return result
