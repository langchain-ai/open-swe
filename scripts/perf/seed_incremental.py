"""Seed an exported thread one message per checkpoint, mimicking a real run's history.

Usage: LANGGRAPH_SEED_URL=http://localhost:8123 python seed_incremental.py <thread.json> <state.json> <new-thread-id> [owner]
"""

import asyncio
import json
import os
import sys
import time

from langgraph_sdk import get_client

URL = os.environ.get("LANGGRAPH_SEED_URL", "http://localhost:2024")


async def main() -> None:
    thread = json.load(open(sys.argv[1]))
    state = json.load(open(sys.argv[2]))
    thread_id = sys.argv[3]
    owner = sys.argv[4] if len(sys.argv) > 4 else "johannes117"
    client = get_client(url=URL)
    metadata = dict(thread["metadata"])
    metadata["owner_login"] = owner
    metadata["participant_logins"] = {**metadata.get("participant_logins", {}), owner: True}
    metadata.pop("assistant_id", None)
    metadata["graph_id"] = "agent"
    metadata["title"] = f"{metadata.get('title', 'thread')} (incremental)"
    try:
        await client.threads.delete(thread_id)
    except Exception:
        pass
    await client.threads.create(thread_id=thread_id, graph_id="agent", metadata=metadata)
    messages = state["values"]["messages"]
    started = time.perf_counter()
    for i, message in enumerate(messages):
        await client.threads.update_state(thread_id, {"messages": [message]}, as_node="model")
        if (i + 1) % 50 == 0:
            print(f"{i + 1}/{len(messages)} checkpoints, {round(time.perf_counter() - started)}s", flush=True)
    for key, value in state["values"].items():
        if key != "messages":
            await client.threads.update_state(thread_id, {key: value}, as_node="model")
    got = await client.threads.get_state(thread_id)
    print(json.dumps({"thread_id": thread_id, "messages": len(got["values"]["messages"]),
                      "state_bytes": len(json.dumps(got)), "seconds": round(time.perf_counter() - started)}))


asyncio.run(main())
