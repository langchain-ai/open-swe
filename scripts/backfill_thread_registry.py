"""One-time idempotent LangGraph thread to dashboard registry backfill."""

import asyncio

from agent.dashboard.thread_registry import (
    close_thread_registry,
    get_thread_registry,
    thread_create_from_metadata,
)
from agent.dashboard.thread_transcript import messages_to_ui
from agent.utils.thread_ops import langgraph_client

PAGE_SIZE = 500


def legacy_status(thread_status: object, run_status: object) -> str:
    if run_status == "interrupted":
        return "interrupted"
    if thread_status == "busy" or run_status in {"pending", "running"}:
        return "running"
    if run_status in {"error", "failed", "timeout"}:
        return "error"
    if run_status == "success":
        return "finished"
    return "idle"


async def backfill() -> dict[str, int]:
    client = langgraph_client()
    registry = await get_thread_registry()
    scanned = created = 0
    offset = 0
    while True:
        threads = await client.threads.search(limit=PAGE_SIZE, offset=offset)
        if not threads:
            break
        for thread in threads:
            scanned += 1
            thread_id = thread.get("thread_id")
            metadata = thread.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            owner = metadata.get("github_login")
            if not isinstance(thread_id, str) or not isinstance(owner, str) or not owner:
                continue
            existed = await registry.get(thread_id)
            await registry.create(
                thread_create_from_metadata(
                    thread_id,
                    owner,
                    metadata,
                    owner_email=(
                        metadata.get("triggering_user_email")
                        if isinstance(metadata.get("triggering_user_email"), str)
                        else None
                    ),
                )
            )
            created += int(existed is None)
            runs = await client.runs.list(thread_id, limit=1)
            run = runs[0] if runs else None
            run_id = run.get("run_id") if isinstance(run, dict) else None
            run_status = run.get("status") if isinstance(run, dict) else None
            status = legacy_status(thread.get("status"), run_status)
            if isinstance(run_id, str) and status != "idle":
                await registry.transition(thread_id, run_id, "queued")
                if status in {"running", "finished"}:
                    await registry.transition(thread_id, run_id, "running")
                if status != "running":
                    await registry.transition(thread_id, run_id, status)  # type: ignore[arg-type]
            state = await client.threads.get_state(thread_id)
            values = state.get("values") if isinstance(state, dict) else None
            messages = values.get("messages") if isinstance(values, dict) else None
            await registry.append_messages(thread_id, run_id, messages_to_ui(messages))
        if len(threads) < PAGE_SIZE:
            break
        offset += len(threads)
    return {"scanned": scanned, "created": created}


async def main() -> None:
    try:
        print(await backfill())
    finally:
        await close_thread_registry()


if __name__ == "__main__":
    asyncio.run(main())
