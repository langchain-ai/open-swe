"""The runs API ``@langchain/react`` 2.0 calls, served under ``/dashboard/api``.

MDA trusts every request the backend sends with its key, so every route checks the
caller against the thread before it calls the thread's runtime. MDA threads are read
on MDA directly, reads held open on its stream pool; threads from before it through
the loopback, which serves them from this deployment's Agent Server. A runtime's error
comes back as its HTTP status, never an SSE error frame, so the SDK can act on it, and
``Location`` and ``Content-Location`` are paths the SDK resolves under its ``apiUrl``.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Final, Literal

import httpx2
import orjson
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from openswe.dashboard.ttft import SUBMITTED_AT_HEADER, DashboardClient
from openswe.dispatch import SERVER_STREAM, V3_STREAMING_CONFIG_KEY, RunFormat
from openswe.threads.index import IndexedThread
from openswe.threads.principals import Principal
from openswe.threads.proxy import ThreadSend, langgraph_proxy_headers
from openswe.threads.runtimes import RUNTIMES, MdaRun
from openswe.threads.start_request import MultitaskStrategy, SentRun, StartRequest
from openswe.threads.store import THREADS
from openswe.utils.http_pool import pooled_transport
from openswe.utils.thread_ops import langgraph_url

logger = logging.getLogger(__name__)

# Says the message ``runs/stream`` was sent joined the live run instead of starting one.
STEERED_HEADER: Final = "x-robin-steered"
# Names the join a run stream answers, its path under the SDK's ``apiUrl`` and its query.
JOIN_HEADER: Final = "x-robin-join"
_RETURNED_RESPONSE_HEADERS: Final = ("content-type", "cache-control")
_LOOPBACK_TIMEOUT: Final = httpx2.Timeout(30.0, connect=5.0)
# Loopback streams held open by tabs watching threads from before MDA, apart from the
# loopback's other calls.
_LOOPBACK_STREAMS: Final = httpx2.Limits(max_connections=100)
_UNREACHABLE: Final = "the thread's runtime could not be reached"
_run_or_runs: Final = TypeAdapter(list[dict[str, JsonValue]] | dict[str, JsonValue])
_json_value: Final = TypeAdapter(JsonValue)
_CONFIG_EVENTS: Final = frozenset({b"checkpoints", b"debug"})
# What the SDK reads of a streamed message's metadata: the graph it came from, and the
# `task` call of a subagent's message.
_MESSAGE_METADATA_KEYS: Final = frozenset(
    {"langgraph_checkpoint_ns", "checkpoint_ns", "lc_subagent_tool_call_id"}
)
# The thread page's lookup of the run going on a thread, the run it joins.
_RUNNING_RUN: Final = (
    ("status", "running"),
    ("limit", "1"),
    ("select", "run_id"),
    ("select", "kwargs"),
)
_runs: Final = TypeAdapter(list[dict[str, JsonValue]])


class _ClientEvents:
    """A runtime's event stream as a client may read it.

    A checkpoint's ``config`` and ``parent_config`` hold the run's configurable, the run
    token among it, so ``checkpoints`` and ``debug`` events lose both and keep their
    ``checkpoint`` ids. One that can't be read is dropped. Every message chunk carries
    the run's whole metadata, most of each ``messages`` event, so it keeps only the keys
    the SDK reads. Every other event passes through byte for byte, held back only until
    it ends.
    """

    def __init__(self) -> None:
        self._pending = bytearray()
        # Where the search for the end of the pending event resumes, so a large event
        # arriving in many chunks is scanned once.
        self._searched = 0

    def feed(self, chunk: bytes) -> bytes:
        self._pending += chunk
        done: list[bytes] = []
        start = 0
        while (end := self._end_of_event(start)) >= 0:
            done.append(self._readable(bytes(self._pending[start:end])))
            start = end
        # Once per chunk: each event's own cut would move the rest of the chunk every time.
        del self._pending[:start]
        self._searched -= start
        return b"".join(done)

    def rest(self) -> bytes:
        event = bytes(self._pending)
        self._pending.clear()
        return self._readable(event) if event else b""

    def _end_of_event(self, start: int) -> int:
        """Where the event at ``start`` ends, past its blank line, or -1 while it hasn't
        arrived whole. Only line ends are checked, so a chunk of many events is scanned
        once."""
        pending = self._pending
        at = max(start, self._searched - 3)
        while (at := pending.find(b"\n", at)) >= 0:
            for blank_line in (b"\n", b"\r\n"):
                if pending.startswith(blank_line, at + 1):
                    self._searched = at + 1 + len(blank_line)
                    return self._searched
            at += 1
        self._searched = len(pending)
        return -1

    @staticmethod
    def _readable(event: bytes) -> bytes:
        data_at = event.find(b"data:")
        head = event if data_at < 0 else event[:data_at]
        name = next(
            (
                line.partition(b":")[2].strip()
                for line in head.splitlines()
                if line.startswith(b"event:")
            ),
            b"",
        )
        kind = name.partition(b"|")[0]
        if kind == b"messages":
            return _ClientEvents._message(event, name)
        if kind not in _CONFIG_EVENTS:
            return event
        lines = event.splitlines()
        data = _ClientEvents._data(lines)
        try:
            payload = _json_value.validate_json(data)
        except ValidationError:
            # Without the error: it quotes the event, whose config holds the run token.
            logger.warning(
                "Dropped a run stream event that could not be read",
                extra={"stream_event": name.decode(errors="replace"), "event_bytes": len(event)},
            )
            return b""
        for part in (payload, payload.get("payload") if isinstance(payload, dict) else None):
            if isinstance(part, dict):
                part.pop("config", None)
                part.pop("parent_config", None)
        return _ClientEvents._with_data(event, lines, json.dumps(payload).encode())

    @staticmethod
    def _message(event: bytes, name: bytes) -> bytes:
        """A ``messages`` event, a subagent's (``messages|<namespace>``) too, its
        ``[chunk, metadata]`` pair keeping only the metadata the SDK reads. A v3 run's
        frame is a protocol event that carries no run metadata, and passes through as
        sent, as does any other shape."""
        lines = event.splitlines()
        try:
            payload = orjson.loads(_ClientEvents._data(lines))
        except orjson.JSONDecodeError:
            logger.warning(
                "Passed on a run stream message that could not be read",
                extra={"stream_event": name.decode(errors="replace"), "event_bytes": len(event)},
            )
            return event
        if not (isinstance(payload, list) and len(payload) == 2 and isinstance(payload[1], dict)):
            return event
        chunk, metadata = payload
        kept = {key: value for key, value in metadata.items() if key in _MESSAGE_METADATA_KEYS}
        return _ClientEvents._with_data(event, lines, orjson.dumps([chunk, kept]))

    @staticmethod
    def _data(lines: list[bytes]) -> bytes:
        return b"\n".join(
            line.removeprefix(b"data:").removeprefix(b" ")
            for line in lines
            if line.startswith(b"data:")
        )

    @staticmethod
    def _with_data(event: bytes, lines: list[bytes], data: bytes) -> bytes:
        newline = b"\r\n" if b"\r\n" in event else b"\n"
        fields = [line for line in lines if line and not line.startswith(b"data:")]
        fields.append(b"data: " + data)
        return newline.join(fields) + newline * 2


class _HeadCheckpoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    checkpoint_id: str | None = None


class _Head(BaseModel):
    """A thread's head as a ``select=checkpoint`` state read answers it."""

    model_config = ConfigDict(extra="ignore")

    checkpoint: _HeadCheckpoint | None = None


class _ServerInfo(BaseModel):
    """The runtime's ``/info``, as the SDK reads it to join a run."""

    model_config = ConfigDict(extra="ignore")

    flags: dict[str, JsonValue] | None = None

    @property
    def filters_namespaces(self) -> bool:
        """Whether a join may ask for the root graph alone and still learn of its
        subagents: the server streams chosen namespaces and lifecycle events."""
        flags = self.flags or {}
        return flags.get("stream_namespaces") is True and flags.get("stream_lifecycle") is True


class _RunningRun(BaseModel):
    """The run going on a thread as the thread page's lookup finds it, its kwargs cut
    to what a client reads (:meth:`MdaRun.client_view`)."""

    model_config = ConfigDict(extra="ignore")

    run_id: str
    kwargs: dict[str, JsonValue] | None = None

    def join_query(self, server: _ServerInfo, checkpoint_id: str | None) -> httpx2.QueryParams:
        """The query the SDK's ``useStream`` joins the run with as a thread loads
        (``#joinFound`` and ``#joinMode``): from the head the load read, never cancelling
        the run, and of the root graph alone when the server can filter and the run
        streams its subgraphs with lifecycle events in the shape the SDK reads."""
        params: list[tuple[str, str | int | float | None]] = [("cancel_on_disconnect", "0")]
        if server.filters_namespaces and self._streams_subagent_lifecycles():
            params.append(("namespace", ""))
        if checkpoint_id is not None:
            params.append(("checkpoint_id", checkpoint_id))
        return httpx2.QueryParams(params)

    def _streams_subagent_lifecycles(self) -> bool:
        kwargs = self.kwargs or {}
        config = kwargs.get("config")
        configurable = config.get("configurable") if isinstance(config, dict) else None
        # A run streaming v3 frames is joined whole: the SDK reads only its run events.
        if kwargs.get("__protocol_stream_run") is True or (
            isinstance(configurable, dict) and configurable.get(V3_STREAMING_CONFIG_KEY) is True
        ):
            return False
        modes = kwargs.get("stream_mode")
        lifecycle = "lifecycle" in modes if isinstance(modes, list) else modes == "lifecycle"
        return kwargs.get("subgraphs") is True and lifecycle


class SdkCommand(BaseModel):
    """What a run answers an interrupt with: the answers, and the state update and jump
    that come with them, as ``input.respond`` takes them on ``/commands``."""

    model_config = ConfigDict(extra="ignore")

    resume: JsonValue = None
    update: JsonValue = None
    goto: JsonValue = None


class SdkConfig(BaseModel):
    """A run's config as a client may send it: only ``configurable``, of which the backend
    reads the keys ``/commands`` does and builds the rest."""

    model_config = ConfigDict(extra="ignore")

    configurable: dict[str, JsonValue] | None = None


class SdkRun(BaseModel):
    """A run the SDK asks for, with only the fields a client may set.

    Every other field is dropped: ``webhook``, ``context``, ``assistant_id``, the
    interrupt fields, ``if_not_exists`` and ``after_seconds`` among them. The backend
    builds the run's context, which carries MDA's run token, catalog and owner, itself.
    """

    model_config = ConfigDict(extra="ignore")

    input: JsonValue = None
    command: SdkCommand | None = None
    config: SdkConfig | None = None
    metadata: dict[str, JsonValue] | None = None
    stream_mode: str | list[str] | None = None
    stream_subgraphs: bool | None = None
    stream_resumable: bool | None = None
    on_disconnect: Literal["cancel", "continue"] | None = None
    multitask_strategy: MultitaskStrategy | None = None
    checkpoint: dict[str, JsonValue] | None = None
    durability: Literal["sync", "async", "exit"] | None = None

    def run_format(self) -> RunFormat:
        """The runs API format the run streams in: as the client asked, or the server's
        (:data:`SERVER_STREAM`) when it asked for no stream fields, so any client can join
        it. It is always resumable: the stream joins the run after starting it, and only a
        resumable run keeps the events it sent before that for the join to replay."""
        asked = self.model_dump(include={"stream_mode", "stream_subgraphs"}, exclude_none=True)
        return RunFormat.of(
            {
                **(asked or SERVER_STREAM.fields),
                **self.model_dump(include={"checkpoint", "durability"}, exclude_none=True),
                "stream_resumable": True,
            }
        )

    def start(self) -> StartRequest:
        """The run as the backend starts it."""
        configurable = self.config.configurable if self.config is not None else None
        return StartRequest(
            input=self.input,
            configurable=dict(configurable or {}),
            metadata=dict(self.metadata or {}),
            multitask_strategy=self.multitask_strategy,
            command=self.command.model_dump(exclude_none=True) if self.command else None,
            run_format=self.run_format(),
        )

    async def stream(self, thread_id: str, principal: Principal, request: Request) -> Response:
        """Send the run as ``/commands`` sends a message, and stream the run it ended up
        in: a new run, the live run it steered into, or the queued run.

        A missing thread is 404: the client creates it with ``POST /threads`` first.
        """
        sent = await self._send(thread_id, principal, request)
        thread = await ThreadOnRuntime.readable(thread_id, principal)
        if sent.run_id is None:
            # Steered into a run nobody recorded: delivered, with nothing to join.
            return StreamingResponse(
                iter(()), media_type="text/event-stream", headers={STEERED_HEADER: "true"}
            )
        namespaces: list[tuple[str, str | int | float | None]] = [
            ("namespace", value) for value in request.query_params.getlist("namespace")
        ]
        run_path = f"/threads/{thread_id}/runs/{sent.run_id}"
        # A run the message steered into is someone else's to stop.
        cancel = "1" if self.on_disconnect == "cancel" and not sent.steered else "0"
        # Joined from its first event, which a resumable run keeps for a join.
        joined = await thread.stream(
            f"{run_path}/stream",
            query=httpx2.QueryParams([*namespaces, ("cancel_on_disconnect", cancel)]),
            last_event_id="-",
        )
        if joined.status_code >= 400:
            return joined
        # A reconnect rejoins with the namespaces it asked for, as MDA's own runs/stream says.
        reconnect = httpx2.QueryParams(namespaces)
        joined.headers["location"] = f"{run_path}/stream" + (f"?{reconnect}" if reconnect else "")
        joined.headers["content-location"] = run_path
        if sent.steered:
            joined.headers[STEERED_HEADER] = "true"
        return joined

    async def create(self, thread_id: str, principal: Principal, request: Request) -> Response:
        """Send the run as ``/commands`` sends a message, and answer with the run it ended
        up in, unstreamed: a new run on an idle thread, and on a busy one the queued run
        with ``enqueue`` or the live run it steered into without.

        A missing thread is 404, and nothing creates it.
        """
        sent = await self._send(thread_id, principal, request)
        thread = await ThreadOnRuntime.readable(thread_id, principal)
        if sent.run_id is None:
            # Steered into a run nobody recorded: delivered, with no run to answer with.
            return Response(status_code=202, headers={STEERED_HEADER: "true"})
        run = await thread.runs(f"/threads/{thread_id}/runs/{sent.run_id}")
        if sent.steered:
            run.headers[STEERED_HEADER] = "true"
        return run

    async def _send(self, thread_id: str, principal: Principal, request: Request) -> SentRun:
        """The run the message ended up in. A refusal (:class:`RunRefused`) reaches the
        client as its HTTP status: the runtime's own when it refused."""
        return await ThreadSend(
            thread_id,
            principal,
            self.start(),
            creates=False,
            client=DashboardClient.of_user_agent(request.headers.get("user-agent")),
            submitted_at=request.headers.get(SUBMITTED_AT_HEADER),
        ).send()


class ThreadOnRuntime:
    """One thread on the runtime it runs on, read for a caller allowed to read it."""

    def __init__(self, thread: IndexedThread) -> None:
        self.thread = thread

    @classmethod
    async def readable(cls, thread_id: str, principal: Principal) -> ThreadOnRuntime:
        """The thread, when ``principal`` may read it; 404 for everyone else alike."""
        thread = await THREADS.get(thread_id)
        if thread is None:
            raise HTTPException(404, "thread not found")
        principal.assert_can_read(thread.metadata)
        return cls(thread)

    async def runs(self, path: str, params: httpx2.QueryParams | None = None) -> Response:
        """One of the thread's runs, or a page of them, each as a client may see it
        (:meth:`MdaRun.client_view`)."""
        async with self._client(held=False) as client:
            try:
                response = await client.get(path, params=params)
            except httpx2.TransportError as exc:
                raise HTTPException(502, _UNREACHABLE) from exc
        if response.is_error:
            return Response(
                response.content,
                status_code=response.status_code,
                headers=self.client_headers(response),
            )
        answer = _run_or_runs.validate_json(response.content)
        return JSONResponse(
            [MdaRun.client_view(run) for run in answer]
            if isinstance(answer, list)
            else MdaRun.client_view(answer),
            status_code=response.status_code,
        )

    async def stream_as_sent(self, path: str, request: Request) -> Response:
        """A read held open for ``request``, with its query and ``Last-Event-ID`` as sent."""
        return await self.stream(
            path, query=request.url.query, last_event_id=request.headers.get("last-event-id")
        )

    async def join(self, run_id: str, request: Request) -> Response:
        """Rejoin one of the thread's runs, and only one of the thread's, to watch it.

        MDA skips its own check that the run is the thread's for a join that sends
        ``Last-Event-ID``, so any run other than the thread's server-recorded
        ``latest_run_id`` is read here first. The join never cancels the run when the
        caller leaves, whatever ``cancel_on_disconnect`` it sent: a caller who may
        only read the thread must not stop its run, which the cancel route checks.
        """
        run_path = f"/threads/{self.thread.thread_id}/runs/{run_id}"
        latest_run_id = self.thread.metadata.get("latest_run_id")
        if not (
            self.thread.runtime == "mda"
            and isinstance(latest_run_id, str)
            and latest_run_id == run_id
        ):
            async with self._client(held=False) as client:
                try:
                    run = await client.get(run_path)
                except httpx2.TransportError as exc:
                    raise HTTPException(502, _UNREACHABLE) from exc
            if run.is_error:
                return Response(
                    run.content, status_code=run.status_code, headers=self.client_headers(run)
                )
        query = [
            (name, value)
            for name, value in request.query_params.multi_items()
            if name != "cancel_on_disconnect"
        ]
        return await self.stream(
            f"{run_path}/stream",
            query=httpx2.QueryParams([*query, ("cancel_on_disconnect", "0")]),
            last_event_id=request.headers.get("last-event-id"),
        )

    async def join_running(self, request: Request) -> Response:
        """Join the run going on the thread as the thread page joins it once its state read
        and running lookup answer, for a page opening the thread to take before then.
        :data:`JOIN_HEADER` names the join, and the page takes the stream only when it
        would ask for the same one. 204 when no run is going on MDA.
        """
        if self.thread.runtime != "mda":
            return Response(status_code=204)
        thread_path = f"/threads/{self.thread.thread_id}"
        async with self._client(held=False) as client:
            try:
                running, head, (info_status, info, _) = await asyncio.gather(
                    client.get(f"{thread_path}/runs", params=_RUNNING_RUN),
                    client.get(f"{thread_path}/state", params=[("select", "checkpoint")]),
                    RUNTIMES.mda_runtime.info(),
                )
            except httpx2.TransportError as exc:
                raise HTTPException(502, _UNREACHABLE) from exc
        if running.is_error or head.is_error:
            return Response(status_code=204)
        try:
            runs = [
                _RunningRun.model_validate(MdaRun.client_view(run))
                for run in _runs.validate_json(running.content)
            ]
            checkpoint = _Head.model_validate_json(head.content).checkpoint
            server = _ServerInfo.model_validate_json(info) if info_status == 200 else _ServerInfo()
        except ValidationError:
            # Without the error: it quotes the run's kwargs, whose context holds the run token.
            logger.warning(
                "Could not read the run going on a thread to join it early",
                extra={"thread_id": self.thread.thread_id},
            )
            return Response(status_code=204)
        if not runs:
            return Response(status_code=204)
        path = f"{thread_path}/runs/{runs[0].run_id}/stream"
        query = runs[0].join_query(server, checkpoint.checkpoint_id if checkpoint else None)
        joined = await self.stream(
            path, query=query, last_event_id=request.headers.get("last-event-id")
        )
        if joined.status_code == 200:
            joined.headers[JOIN_HEADER] = f"{path}?{query}"
        return joined

    async def stream(
        self, path: str, *, query: httpx2.QueryParams | str, last_event_id: str | None
    ) -> Response:
        """A read held open, streamed back until the runtime or the caller ends it, its
        events as a client may read them (:class:`_ClientEvents`).

        A refusal comes back with the runtime's status, before anything streams.
        """
        client = self._client(held=True)
        headers = {"accept": "text/event-stream"}
        if last_event_id is not None:
            headers["last-event-id"] = last_event_id
        try:
            response = await client.send(
                client.build_request("GET", path, params=query, headers=headers), stream=True
            )
        except BaseException as exc:
            await client.aclose()
            if isinstance(exc, httpx2.TransportError):
                raise HTTPException(502, _UNREACHABLE) from exc
            raise
        if response.is_error:
            try:
                content = await response.aread()
            finally:
                await response.aclose()
                await client.aclose()
            return Response(
                content, status_code=response.status_code, headers=self.client_headers(response)
            )

        async def events() -> AsyncIterator[bytes]:
            readable = _ClientEvents()
            try:
                async for chunk in response.aiter_bytes():
                    if sent := readable.feed(chunk):
                        yield sent
                if rest := readable.rest():
                    yield rest
            finally:
                await response.aclose()
                await client.aclose()

        return StreamingResponse(
            events(), status_code=response.status_code, headers=self.client_headers(response)
        )

    def _client(self, *, held: bool) -> httpx2.AsyncClient:
        """The runtime's client. Nothing here makes the thread on MDA first, a call that
        costs every read: a run's start made sure of its thread, a run can't be on a thread
        MDA lacks, and the thread page's ``/state`` read makes the backend's own again."""
        if self.thread.runtime == "mda":
            mda = RUNTIMES.mda_runtime
            return mda.stream_http if held else mda.read_http
        # Threads from before MDA, which this deployment's Agent Server keeps.
        return httpx2.AsyncClient(
            base_url=langgraph_url(),
            headers=langgraph_proxy_headers(),
            timeout=httpx2.Timeout(None) if held else _LOOPBACK_TIMEOUT,
            # A pool of their own when held, so open tabs never starve other requests.
            transport=pooled_transport("loopback-stream", limits=_LOOPBACK_STREAMS)
            if held
            else pooled_transport(),
        )

    @staticmethod
    def client_headers(response: httpx2.Response) -> dict[str, str]:
        """The runtime's response headers a client reads, its paths resolving under the
        SDK's ``apiUrl``: the runtime's ``/threads/...`` path, whatever came before it."""
        headers = {
            name: value
            for name in _RETURNED_RESPONSE_HEADERS
            if (value := response.headers.get(name)) is not None
        }
        for name in ("location", "content-location"):
            if (value := response.headers.get(name)) is not None:
                start = value.find("/threads/")
                headers[name] = value[start:] if start >= 0 else value
        return headers
