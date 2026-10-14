"""Streaming a remotely hosted agent's replies into its Open SWE transcript.

A run hosted elsewhere, such as on Managed Deep Agents, calls the model itself,
so the backend never sees the deltas. ``RemoteTranscriptMiddleware`` captures
them as the backend's own transcript middleware does, and ``HttpFragmentSink``
posts them to the backend's ``PATH`` with the run's token. The backend records
everything else about the run (its turn, completed messages and tool calls)
from the run's hooks, and completes each streamed message under the id the
reply names (``mark_streamed``).

Streaming is best effort. A batch the backend could not take is retried a few
times and then dropped with a log, and nothing here ever fails the run:
``message.completed`` replaces whatever streamed, so a lost fragment heals when
its message completes.
"""

import asyncio
import itertools
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Final, Protocol
from uuid import uuid4

import httpx2
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from openswe_transcript.stream import StreamCapture, current_namespace, tool_namespace
from openswe_transcript.wire import (
    MAX_BATCH_FRAGMENTS,
    PATH,
    Fragment,
    FragmentBatch,
    mark_streamed,
)

logger = logging.getLogger(__name__)

_RETRY_DELAYS_SECONDS: Final = (0.25, 1.0, 4.0)
_REQUEST_TIMEOUT_SECONDS: Final = 10.0
# A model call waits this long for its fragments to land before it returns.
_FLUSH_TIMEOUT_SECONDS: Final = 10.0
# The writer holds its connection open across the pauses between a reply's paragraphs.
_IDLE_SECONDS: Final = 30.0


def current_run_id() -> str | None:
    """The Agent Server run executing right now, as the run's hooks name it to the backend.

    ``config["run_id"]`` is the tracer's run, not the server's.
    """
    try:
        config = get_config()
    except RuntimeError:
        return None
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    raw = configurable.get("run_id") or metadata.get("run_id")
    return str(raw) if raw else None


class FragmentSink(Protocol):
    """Where a remote runtime's fragments go."""

    def send(self, run_id: str, fragment: Fragment) -> None: ...

    async def flush(self) -> None:
        """Return once every fragment sent so far was delivered or dropped."""
        ...


class HttpFragmentSink:
    """Posts one run's fragments to the backend, in order, from a single writer task.

    It holds the run's token, so it serves that run alone. The writer keeps one
    connection open while fragments flow and exits once they stop.
    """

    def __init__(
        self,
        backend_url: str,
        run_token: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = f"{backend_url.rstrip('/')}{PATH}"
        self._transport = transport
        self._headers = {"Authorization": f"Bearer {run_token}", "Content-Type": "application/json"}
        self._queue: asyncio.Queue[tuple[str, Fragment]] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None
        self._refused = False

    def send(self, run_id: str, fragment: Fragment) -> None:
        if self._refused:
            return
        self._queue.put_nowait((run_id, fragment))
        if self._writer is None or self._writer.done():
            self._writer = asyncio.create_task(self._write())

    async def flush(self) -> None:
        try:
            await asyncio.wait_for(self._queue.join(), timeout=_FLUSH_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(
                "Transcript fragments are still in flight",
                extra={"transcript_pending": self._queue.qsize()},
            )

    async def _write(self) -> None:
        try:
            async with httpx2.AsyncClient(
                timeout=_REQUEST_TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                while await self._deliver_next(client):
                    pass
        except Exception:
            logger.warning("The transcript fragment writer stopped", exc_info=True)

    async def _deliver_next(self, client: httpx2.AsyncClient) -> bool:
        """Deliver the next batch, or return ``False`` once fragments stopped arriving."""
        try:
            first = await asyncio.wait_for(self._queue.get(), timeout=_IDLE_SECONDS)
        except TimeoutError:
            # Nothing awaits between here and the writer returning, so a fragment
            # sent from now on starts a new writer.
            self._writer = None
            return False
        items = [first]
        while len(items) < MAX_BATCH_FRAGMENTS and not self._queue.empty():
            items.append(self._queue.get_nowait())
        try:
            for run_id, group in itertools.groupby(items, key=lambda item: item[0]):
                await self._post(
                    client, FragmentBatch(run_id=run_id, fragments=[f for _, f in group])
                )
        except Exception:
            logger.warning("Transcript fragment delivery failed", exc_info=True)
        finally:
            for _ in items:
                self._queue.task_done()
        return True

    async def _post(self, client: httpx2.AsyncClient, batch: FragmentBatch) -> None:
        if self._refused:
            return
        body = batch.model_dump_json()
        for delay in (*_RETRY_DELAYS_SECONDS, None):
            try:
                response = await client.post(self._url, headers=self._headers, content=body)
            except httpx2.TransportError as exc:
                # Named by type only: transport errors can echo the request, and it carries the token.
                failure = type(exc).__name__
            else:
                if response.is_success:
                    return
                if response.status_code != 429 and response.status_code < 500:
                    # The token, the run or its turn was refused, and every later batch would be.
                    self._refused = True
                    logger.warning(
                        "The backend refused transcript fragments; this run stops streaming",
                        extra={
                            "status_code": response.status_code,
                            "transcript_run_id": batch.run_id,
                        },
                    )
                    return
                failure = f"HTTP {response.status_code}"
            if delay is None:
                logger.warning(
                    "Dropped transcript fragments the backend did not take",
                    extra={"failure": failure, "transcript_fragments": len(batch.fragments)},
                )
                return
            await asyncio.sleep(delay)


class RemoteTranscriptMiddleware(AgentMiddleware):
    """Stream a remotely hosted agent's replies into its Open SWE transcript as they are written.

    Goes on the agent and on each of its subagents, inside the middleware that
    hands each reply to the backend: a reply's fragments are flushed before the
    model call returns, so they reach the backend ahead of its completion.
    """

    def __init__(self, sink: FragmentSink) -> None:
        self._sink = sink

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        run_id = current_run_id()
        if run_id is None:
            return await handler(request)
        capture = StreamCapture(str(uuid4()), current_namespace(), partial(self._sink.send, run_id))
        with capture.attached():
            response = await handler(request)
        if not capture.streamed:
            return response
        capture.finish()
        reply = next((m for m in response.result if isinstance(m, AIMessage)), None)
        if reply is not None:
            mark_streamed(reply, capture.message_id)
        await self._sink.flush()
        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_call_id = request.tool_call.get("id")
        if not tool_call_id:
            return await handler(request)
        # A ``task`` call's subagent streams under its id.
        with tool_namespace(tool_call_id):
            return await handler(request)
