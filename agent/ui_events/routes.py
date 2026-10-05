"""The dashboard's one change stream: which subscribed topics changed, as they change.

A browser opens it with every topic its mounted queries read, each paired with
how many seconds ago that query's data was last known current. The stream
first reports which of those topics changed within their window, then every
change as it commits. Frames carry topics only; the browser refetches.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from agent.dashboard.deps import SESSION_DEP
from agent.ui_events import hub, outbox, topics
from agent.utils.build_info import backend_build_info

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ui-events"])

HEARTBEAT_SECONDS = 15.0
MAX_TOPICS = 256
_COALESCE_SECONDS = 0.1
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get("/ui-events")
async def api_ui_events(
    t: Annotated[list[str] | None, Query()] = None,
    session: dict[str, Any] = SESSION_DEP,
) -> StreamingResponse:
    """``t`` is repeated ``<age seconds>.<topic>``, e.g. ``t=42.workspaces``."""
    ages = _parse_subscriptions(t or [])
    allowed = await asyncio.gather(*(topics.may_hear(session, topic) for topic in ages))
    heard = {topic: age for (topic, age), ok in zip(ages.items(), allowed, strict=True) if ok}
    denied = sorted(set(ages) - set(heard))
    return StreamingResponse(
        _stream(heard, denied), media_type="text/event-stream", headers=_SSE_HEADERS
    )


def _parse_subscriptions(values: list[str]) -> dict[str, float]:
    if len(values) > MAX_TOPICS:
        raise HTTPException(400, f"at most {MAX_TOPICS} topics per stream")
    ages: dict[str, float] = {}
    for value in values:
        age, _, topic = value.partition(".")
        if not age.isdigit() or not topics.valid(topic):
            raise HTTPException(400, f"unreadable subscription {value[:80]!r}")
        ages[topic] = max(ages.get(topic, 0.0), float(age))
    return ages


async def _stream(ages: dict[str, float], denied: list[str]) -> AsyncGenerator[str]:
    """``hello`` names what changed while the reader was away; it is synchronized from then on."""
    with hub.subscribe(frozenset(ages)) as stream:
        windows = {topic: age + hub.REPLAY_MARGIN_SECONDS for topic, age in ages.items()}
        try:
            stream.mark(await outbox.changed_since(windows))
        except Exception:  # noqa: BLE001
            # Without a replay the reader cannot know what it missed, so it is
            # told everything changed: a refetch too many, never a stale page.
            logger.warning("Replaying UI events failed", exc_info=True)
            stream.mark(ages)
        yield _frame(
            "hello",
            {
                "backend_commit": backend_build_info()["commit"],
                "changed": sorted(stream.take()),
                "denied": denied,
            },
        )
        while True:
            changed = await stream.drain(HEARTBEAT_SECONDS)
            if not changed:
                yield _frame("alive", {})
                continue
            await asyncio.sleep(_COALESCE_SECONDS)
            changed |= stream.take()
            yield _frame("changed", {"topics": sorted(changed)})


def _frame(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
