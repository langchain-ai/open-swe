"""Server-sent events for a progressive AI search: the client re-ranks as batches arrive."""

import json
import logging
from collections.abc import AsyncIterator, Callable

from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from langchain_typesafe.client import TypeSafeError

from openswe.utils.jev_search import JevBatch

logger = logging.getLogger(__name__)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def ai_search_response[ItemT](
    batches: AsyncIterator[JevBatch[ItemT]], render: Callable[[ItemT, float], object]
) -> StreamingResponse:
    """Stream ``batch`` events (new matches and the running ``scanned`` count), then
    ``done`` or ``failed`` (not ``error``, which ``EventSource`` uses for a dropped
    connection). A client that disconnects cancels the search mid-page."""
    return StreamingResponse(
        _frames(batches, render), media_type="text/event-stream", headers=_SSE_HEADERS
    )


async def _frames[ItemT](
    batches: AsyncIterator[JevBatch[ItemT]], render: Callable[[ItemT, float], object]
) -> AsyncIterator[str]:
    scanned = 0
    try:
        async for batch in batches:
            scanned = batch.scanned
            yield _frame(
                "batch",
                {
                    "matches": [render(match.item, match.relevance) for match in batch.matches],
                    "scanned": scanned,
                },
            )
    except TypeSafeError, TimeoutError:
        logger.exception("AI search failed", extra={"scanned": scanned})
        yield _frame("failed", {"message": "AI search failed", "scanned": scanned})
        return
    yield _frame("done", {"scanned": scanned})


def _frame(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data))}\n\n"
