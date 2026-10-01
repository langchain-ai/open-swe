"""Follow one run's transcript until the response it answers is settled."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from agent.openai_responses.ids import OpenSweId
from agent.openai_responses.models import Response, StreamEvent
from agent.openai_responses.projection import ResponseProjection
from agent.transcript import listener
from agent.transcript.snapshot import load_events
from agent.utils.thread_ops import langgraph_client

# Clients such as Codex drop a stream that is silent for minutes, and an agent's
# tool call can take that long.
HEARTBEAT_SECONDS = 15.0
_PAGE = 500
_ENDED_RUN_STATUSES = frozenset({"success", "error", "timeout", "interrupted"})


@dataclass(slots=True)
class ResponseRun:
    ids: OpenSweId
    projection: ResponseProjection
    after: int

    @property
    def thread_id(self) -> str:
        return self.ids.thread_id

    async def events(self) -> AsyncIterator[StreamEvent]:
        for event in self.projection.start():
            yield event
        cursor = self.after
        run_ended = False
        async with listener.subscribe(self.thread_id) as notifications:
            while True:
                page = await load_events(self.thread_id, after=cursor, limit=_PAGE)
                for stored in page:
                    cursor = stored.version
                    for event in await self.projection.apply(stored):
                        yield event
                    if self.projection.done:
                        return
                if len(page) == _PAGE:
                    continue
                # The transcript's terminal event is best-effort; a run that ended
                # without one would otherwise keep this stream open forever.
                if run_ended:
                    message = (
                        "The run ended without finishing its response"
                        if self.projection.started
                        else "The run ended before it started"
                    )
                    for event in self.projection.abandon(message):
                        yield event
                    return
                try:
                    async with asyncio.timeout(HEARTBEAT_SECONDS):
                        version = await anext(notifications)
                except TimeoutError:
                    yield self.projection.heartbeat()
                    run_ended = await self._run_ended()
                    continue
                if version == listener.DELETED_VERSION:
                    for event in self.projection.abandon("The conversation was deleted"):
                        yield event
                    return

    async def sse(self) -> AsyncIterator[str]:
        async for event in self.events():
            yield event.sse()

    async def result(self) -> Response:
        async for _ in self.events():
            pass
        return self.projection.response

    async def snapshot(self) -> Response:
        """What the transcript holds so far, without waiting for more."""
        cursor = self.after
        while True:
            page = await load_events(self.thread_id, after=cursor, limit=_PAGE)
            for stored in page:
                cursor = stored.version
                await self.projection.apply(stored)
            if len(page) < _PAGE:
                break
        response = self.projection.response
        if not self.projection.done:
            response.status = "in_progress" if self.projection.started else "queued"
        return response

    async def _run_ended(self) -> bool:
        run = await langgraph_client().runs.get(self.thread_id, str(self.ids.run_id))
        return run["status"] in _ENDED_RUN_STATUSES
