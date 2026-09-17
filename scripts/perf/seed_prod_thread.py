"""Seed an exported production thread (thread + state JSON) into the local langgraph dev server.

Usage: python seed_prod_thread.py <thread.json> <state.json> [owner_login]
"""

import asyncio
import json
import sys
import time

from langgraph_sdk import get_client

import os

URL = os.environ.get("LANGGRAPH_SEED_URL", "http://localhost:2024")


async def main() -> None:
    thread = json.load(open(sys.argv[1]))
    state = json.load(open(sys.argv[2]))
    owner = sys.argv[3] if len(sys.argv) > 3 else "johannes117"
    client = get_client(url=URL)
    thread_id = thread["thread_id"]
    metadata = dict(thread["metadata"])
    metadata["owner_login"] = owner
    metadata["participant_logins"] = {**metadata.get("participant_logins", {}), owner: True}
    metadata.pop("assistant_id", None)
    metadata["graph_id"] = "agent"
    try:
        await client.threads.delete(thread_id)
    except Exception:
        pass
    await client.threads.create(thread_id=thread_id, graph_id="agent", metadata=metadata)
    started = time.perf_counter()
    await client.threads.update_state(thread_id, state["values"], as_node="model")
    update_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    got = await client.threads.get_state(thread_id)
    state_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    row = await client.threads.get(thread_id)
    get_ms = (time.perf_counter() - started) * 1000
    print(json.dumps({
        "thread_id": thread_id,
        "messages": len(got["values"]["messages"]),
        "state_bytes": len(json.dumps(got)),
        "row_values_bytes": len(json.dumps(row.get("values"))),
        "row_status": row["status"],
        "update_state_ms": round(update_ms),
        "get_state_ms": round(state_ms),
        "threads_get_ms": round(get_ms),
    }))


asyncio.run(main())
