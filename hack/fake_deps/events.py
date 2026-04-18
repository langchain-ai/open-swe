"""Shared SSE event-bus for fake_deps.

Single process-wide set of subscriber queues; Slack and GitHub routers push
events here, the UI's EventSource consumes them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

SUBSCRIBERS: set[asyncio.Queue[dict[str, Any]]] = set()


async def broadcast(event: dict[str, Any]) -> None:
    stale = []
    for q in list(SUBSCRIBERS):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            stale.append(q)
    for q in stale:
        SUBSCRIBERS.discard(q)


async def sse_stream(request):
    from fastapi import Request  # local import to avoid cycle

    assert isinstance(request, Request)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
    SUBSCRIBERS.add(queue)
    try:
        yield "event: ping\ndata: {}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25.0)
                yield f"data: {json.dumps(event)}\n\n"
            except TimeoutError:
                yield ": keepalive\n\n"
    finally:
        SUBSCRIBERS.discard(queue)
