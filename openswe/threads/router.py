"""Serve the Agent Server's thread API from the PostgreSQL thread index.

``ThreadIndexMirror`` keeps the index beside the Agent Server with
``THREAD_STORE=dual``. With ``THREAD_STORE=postgres``, ``ThreadRouter`` sits where
``MdaThreadProxy`` does, in front of the core ``/threads`` and ``/runs`` routes, so every client
(the dashboard, the CLI and the backend's own ``client.threads``) keeps working
unchanged:

- Creating, reading, updating, deleting, searching and counting threads is
  answered from the index; each change is copied to the runtimes that keep the
  thread, so its runs and traces see it and the old proxy could take over again.
- A thread's runs, state, history, commands and streams go to its runtime: this
  deployment's Agent Server for ``local`` threads, Managed Deep Agents for
  ``mda`` ones. A run started there marks the thread busy; the run-complete
  webhook and a reconcile job settle it.
- A thread the index does not know yet is read from the Agent Server, indexed,
  and served from then on.
"""

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Final, Literal

from pydantic import JsonValue, TypeAdapter, ValidationError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from openswe.api.agent_server_auth import backend_call
from openswe.config import ENV
from openswe.jobs import queue
from openswe.remote_runtime.asgi import (
    StatusRecorder,
    configurable_of,
    forward,
    json_body,
    read_body,
    replay,
    send_json,
)
from openswe.remote_runtime.client import (
    HOSTED_GRAPHS,
    MDA_GRAPH_ID,
    MDA_RUNTIME,
    RUNTIME_METADATA_KEY,
)
from openswe.remote_runtime.proxy import LOCAL_HEADER, MdaRuntime, runs_on_mda
from openswe.threads import index
from openswe.threads.index import IndexedThread, ThreadExistsError, ThreadSearch
from openswe.threads.runtimes import RUNTIMES

logger = logging.getLogger(__name__)

_object = TypeAdapter(dict[str, JsonValue])

_THREADS: Final = re.compile(r"^(?:/noauth)?/threads/?$")
_SEARCH: Final = re.compile(r"^(?:/noauth)?/threads/search$")
_COUNT: Final = re.compile(r"^(?:/noauth)?/threads/count$")
_THREAD: Final = re.compile(r"^(?:/noauth)?/threads/(?P<thread_id>[0-9a-fA-F-]{36})(?P<rest>/.*)?$")
_CANCEL_MANY: Final = re.compile(r"^(?:/noauth)?/runs/cancel$")
_CREATE_RUN: Final = re.compile(r"^/runs(?:/stream|/wait)?$")
_CREATE_CRON: Final = "/runs/crons"
THREAD_SYNC_JOB: Final = "thread_index_sync"


def _routed(scope: Scope) -> bool:
    path: str = scope["path"]
    if _THREAD.match(path):
        return True
    if scope["method"] != "POST":
        return False
    return bool(
        _THREADS.match(path)
        or _SEARCH.match(path)
        or _COUNT.match(path)
        or _CANCEL_MANY.match(path)
    )


def _for_mda(metadata: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Metadata as MDA keeps it: there every Open SWE graph is ``open-swe``."""
    copied = dict(metadata)
    if "graph_id" in copied:
        copied["graph_id"] = MDA_GRAPH_ID
    return copied


def _marks_busy(thread_id: str) -> Callable[[], Awaitable[None]]:
    async def mark() -> None:
        await index.set_status(thread_id, "busy")

    return mark


async def _not_found(send: Send, thread_id: str) -> None:
    await send_json(send, 404, {"detail": f"Thread {thread_id} not found"})


class ThreadRouter:
    """ASGI middleware answering thread calls from the index and routing runs by runtime."""

    def __init__(self, app: ASGIApp) -> None:
        from langgraph_api.auth.middleware import auth_middleware

        self.app = app
        # The Agent Server authenticates at its own routes, behind app middleware
        # like this one, so requests handled here pass the same check first.
        self._authenticated: ASGIApp = auth_middleware.cls(
            self._route, *auth_middleware.args, **auth_middleware.kwargs
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or Headers(scope=scope).get(LOCAL_HEADER) or not _routed(scope):
            await self.app(scope, receive, send)
            return
        await self._authenticated(scope, receive, send)

    async def _route(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not backend_call(scope):
            # The Agent Server, which keeps a copy of every thread, applies its rules to others.
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        method: str = scope["method"]
        body = await read_body(receive)
        replayed = replay(body, receive)
        if _SEARCH.match(path):
            await self._search(send, body, count=False)
        elif _COUNT.match(path):
            await self._search(send, body, count=True)
        elif _THREADS.match(path):
            await self._create(send, body)
        elif _CANCEL_MANY.match(path):
            await self._cancel_many(scope, replayed, send, body)
        elif (match := _THREAD.match(path)) is not None:
            thread_id, rest = match["thread_id"], match["rest"] or ""
            if rest in {"", "/"}:
                await self._thread_itself(scope, replayed, send, body, thread_id, method)
            elif method == "POST" and (_CREATE_RUN.match(rest) or rest == _CREATE_CRON):
                await self._create_run(
                    scope, replayed, send, body, thread_id, cron=rest == _CREATE_CRON
                )
            elif method == "POST" and rest == "/commands":
                await self._command(scope, replayed, send, body, thread_id)
            elif method == "POST" and rest == "/copy":
                await self._copy(send, thread_id)
            elif method == "POST" and rest == "/state":
                await self._write_state(scope, replayed, send, body, thread_id)
            else:
                await self._to_runtime(scope, replayed, send, body, thread_id)

    async def _thread(self, thread_id: str) -> IndexedThread | None:
        """The indexed thread; one only the Agent Server knows is indexed on first use."""
        indexed = await index.get(thread_id)
        if indexed is not None:
            return indexed
        local = await RUNTIMES.local.indexed(thread_id)
        if local is None:
            return None
        return await index.create(
            thread_id,
            runtime=local.runtime,
            metadata=local.metadata,
            if_exists="do_nothing",
            status=local.status,
            created_at=local.created_at,
            updated_at=local.updated_at,
        )

    async def _create(self, send: Send, body: bytes) -> None:
        payload = json_body(body)
        if payload is None:
            await send_json(send, 422, {"detail": "The body must be a JSON object"})
            return
        given_id = payload.get("thread_id")
        thread_id = given_id if isinstance(given_id, str) and given_id else str(uuid.uuid4())
        given_metadata = payload.get("metadata")
        metadata = given_metadata if isinstance(given_metadata, dict) else {}
        if_exists: Literal["raise", "do_nothing"] = (
            "do_nothing" if payload.get("if_exists") == "do_nothing" else "raise"
        )
        try:
            thread = await self._index_new(thread_id, metadata, if_exists=if_exists)
        except ThreadExistsError:
            await send_json(send, 409, {"detail": f"Thread {thread_id} already exists"})
            return
        await send_json(send, 200, thread.as_langgraph())

    async def _index_new(
        self,
        thread_id: str,
        metadata: Mapping[str, JsonValue],
        *,
        if_exists: Literal["raise", "do_nothing"],
    ) -> IndexedThread:
        if if_exists == "do_nothing" and (known := await self._thread(thread_id)) is not None:
            return known
        thread = await index.create(
            thread_id, runtime="local", metadata=metadata, if_exists=if_exists
        )
        await RUNTIMES.local.create(thread_id, thread.metadata)
        return thread

    async def _thread_itself(
        self, scope: Scope, receive: Receive, send: Send, body: bytes, thread_id: str, method: str
    ) -> None:
        thread = await self._thread(thread_id)
        if thread is None:
            await _not_found(send, thread_id)
            return
        if method == "GET":
            await send_json(send, 200, thread.as_langgraph())
        elif method == "PATCH":
            payload = json_body(body) or {}
            metadata = payload.get("metadata")
            updated = await index.update(thread_id, metadata if isinstance(metadata, dict) else {})
            if updated is None:
                await _not_found(send, thread_id)
                return
            await self._copy_metadata(updated, metadata if isinstance(metadata, dict) else {})
            await send_json(send, 200, updated.as_langgraph())
        elif method == "DELETE":
            for runtime in reversed(RUNTIMES.copies(thread.runtime)):
                await runtime.delete(thread_id)
            await index.delete(thread_id)
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        else:
            await self.app(scope, receive, send)

    async def _copy_metadata(self, thread: IndexedThread, patch: Mapping[str, JsonValue]) -> None:
        if not patch:
            return
        for runtime in RUNTIMES.copies(thread.runtime):
            try:
                await runtime.update(
                    thread.thread_id, _for_mda(patch) if runtime is not RUNTIMES.local else patch
                )
            except Exception:
                # Reads come from the index; a runtime's copy only feeds its traces.
                logger.warning(
                    "Copying thread metadata to its runtime failed",
                    extra={"thread_id": thread.thread_id},
                    exc_info=True,
                )

    async def _search(self, send: Send, body: bytes, *, count: bool) -> None:
        try:
            query = ThreadSearch.model_validate(json_body(body) or {})
        except ValidationError as exc:
            await send_json(send, 422, {"detail": str(exc)})
            return
        if count:
            await send_json(send, 200, await query.count())
            return
        await send_json(
            send, 200, [thread.as_langgraph(query.select) for thread in await query.run()]
        )

    def _mda(self) -> MdaRuntime:
        runtime = RUNTIMES.mda_runtime
        if runtime is None:
            raise LookupError("MDA_RUNTIME_URL is not configured")
        return runtime

    async def _claim_for_mda(
        self, thread: IndexedThread, assistant_id: JsonValue, configurable: Mapping[str, JsonValue]
    ) -> IndexedThread:
        """Move a fresh thread to MDA for its first agent or reviewer run or state.

        With ``MDA_ALL_THREADS`` every such thread moves; otherwise those of people
        (or workspaces) who chose MDA. Other graphs, and threads with checkpoints
        here already, stay on this deployment.
        """
        if (
            thread.runtime == "mda"
            or not isinstance(assistant_id, str)
            or assistant_id not in HOSTED_GRAPHS
            or RUNTIMES.mda_runtime is None
            or await RUNTIMES.local.has_history(thread.thread_id)
            or not (
                ENV.MDA_ALL_THREADS.get_bool() or await runs_on_mda(thread.metadata, configurable)
            )
        ):
            return thread
        await index.set_runtime(thread.thread_id, "mda")
        claimed = await index.update(thread.thread_id, {RUNTIME_METADATA_KEY: MDA_RUNTIME})
        if claimed is None:
            return thread
        await RUNTIMES.local.update(thread.thread_id, {RUNTIME_METADATA_KEY: MDA_RUNTIME})
        await RUNTIMES.mda.create(thread.thread_id, _for_mda(claimed.metadata))
        logger.info("Claimed thread for Managed Deep Agents", extra={"thread_id": thread.thread_id})
        return claimed

    async def _create_run(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        body: bytes,
        thread_id: str,
        *,
        cron: bool,
    ) -> None:
        payload = json_body(body)
        if payload is None:
            await send_json(send, 422, {"detail": "The body must be a JSON object"})
            return
        thread = await self._thread(thread_id)
        if thread is None:
            if payload.get("if_not_exists") != "create":
                await _not_found(send, thread_id)
                return
            thread = await self._index_new(thread_id, {}, if_exists="do_nothing")
        thread = await self._claim_for_mda(
            thread, payload.get("assistant_id"), configurable_of(payload)
        )
        # Busy before the answer reaches the caller, so a run that ends at once
        # is not marked busy after its run-complete webhook settled it.
        recorder = StatusRecorder(send, on_success=None if cron else _marks_busy(thread_id))
        if thread.runtime == "mda":
            mda = self._mda()
            await mda.ensure_remote_thread(thread_id)
            rewritten = await mda.run_payload(thread_id, payload)
            await forward(mda.http, scope, recorder, json.dumps(rewritten).encode())
        else:
            await self.app(scope, receive, recorder)

    async def _command(
        self, scope: Scope, receive: Receive, send: Send, body: bytes, thread_id: str
    ) -> None:
        command = json_body(body)
        thread = await self._thread(thread_id)
        if command is None or thread is None:
            await self._to_runtime(scope, receive, send, body, thread_id)
            return
        params = command.get("params")
        if command.get("method") == "run.start" and isinstance(params, dict):
            thread = await self._claim_for_mda(
                thread, params.get("assistant_id"), configurable_of(params)
            )
            if thread.runtime == "mda":
                reply = await self._mda().start_command_run(thread_id, command, params)
                if reply.get("type") == "success":
                    await index.set_status(thread_id, "busy")
                await send_json(send, 200, reply)
                return
            await self.app(scope, receive, StatusRecorder(send, on_success=_marks_busy(thread_id)))
            return
        await self._to_runtime(scope, receive, send, body, thread_id)

    async def _write_state(
        self, scope: Scope, receive: Receive, send: Send, body: bytes, thread_id: str
    ) -> None:
        """A first state write (an upload, a fork) decides the runtime as a first run would."""
        thread = await self._thread(thread_id)
        if thread is not None:
            graph = thread.metadata.get("graph_id") or "agent"
            await self._claim_for_mda(thread, graph, {})
        await self._to_runtime(scope, receive, send, body, thread_id)

    async def _copy(self, send: Send, thread_id: str) -> None:
        """Copy a thread on its runtime, and index the copy as the source is."""
        thread = await self._thread(thread_id)
        if thread is None:
            await _not_found(send, thread_id)
            return
        made = _object.validate_python(
            await RUNTIMES.runs_on(thread.runtime).client.http.post(
                f"/threads/{thread_id}/copy", json={}
            )
        )
        copy_id = made.get("thread_id")
        if not isinstance(copy_id, str):
            raise RuntimeError("The runtime's thread copy returned no thread id")
        made_metadata = made.get("metadata")
        metadata = dict(made_metadata) if isinstance(made_metadata, dict) else dict(thread.metadata)
        copied = await index.create(
            copy_id, runtime=thread.runtime, metadata=metadata, if_exists="do_nothing"
        )
        if thread.runtime == "mda":
            await RUNTIMES.local.create(copy_id, copied.metadata)
        await send_json(send, 200, copied.as_langgraph())

    async def _to_runtime(
        self, scope: Scope, receive: Receive, send: Send, body: bytes, thread_id: str
    ) -> None:
        thread = await self._thread(thread_id)
        if thread is not None and thread.runtime == "mda":
            mda = self._mda()
            await mda.ensure_remote_thread(thread_id)
            await forward(mda.http, scope, send, body)
            return
        await self.app(scope, receive, send)

    async def _cancel_many(self, scope: Scope, receive: Receive, send: Send, body: bytes) -> None:
        payload = json_body(body) or {}
        thread_id = payload.get("thread_id")
        if isinstance(thread_id, str):
            await self._to_runtime(scope, receive, send, body, thread_id)
            return
        await self.app(scope, receive, send)


class ThreadIndexMirror:
    """ASGI middleware writing each thread change the Agent Server makes to the index too.

    With ``THREAD_STORE=dual`` the Agent Server, behind ``MdaThreadProxy``, still
    answers every thread call. Once it has made a change, the thread is copied
    here as the Agent Server now has it, and a started run marks it busy, so
    moving reads to the index finds it current. A copy that fails is retried
    from a job; the caller already has the Agent Server's answer.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or Headers(scope=scope).get(LOCAL_HEADER) or not _routed(scope):
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        method: str = scope["method"]
        match = _THREAD.match(path)
        if match is None:
            if method == "POST" and _THREADS.match(path):
                await self._made(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return
        thread_id, rest = match["thread_id"], match["rest"] or ""
        if rest in {"", "/"} and method in {"PATCH", "DELETE"}:
            recorder = StatusRecorder(send)
            await self.app(scope, receive, recorder)
            if recorder.succeeded:
                await _sync_soon(thread_id)
        elif method == "POST" and rest == "/copy":
            await self._made(scope, receive, send)
        elif method == "POST" and _CREATE_RUN.match(rest):
            await self.app(
                scope, receive, StatusRecorder(send, on_success=self._started(thread_id))
            )
        elif method == "POST" and rest == "/commands":
            body = await read_body(receive)
            command = json_body(body) or {}
            started = self._started(thread_id) if command.get("method") == "run.start" else None
            await self.app(scope, replay(body, receive), StatusRecorder(send, on_success=started))
        else:
            await self.app(scope, receive, send)

    async def _made(self, scope: Scope, receive: Receive, send: Send) -> None:
        """A thread created or copied: indexed by the id the Agent Server answered with."""
        recorder = StatusRecorder(send, keep_body=True)
        await self.app(scope, receive, recorder)
        made = json_body(recorder.body) if recorder.succeeded else None
        thread_id = made.get("thread_id") if made else None
        if isinstance(thread_id, str):
            await _sync_soon(thread_id)

    def _started(self, thread_id: str) -> Callable[[], Awaitable[None]]:
        async def started() -> None:
            if await _sync_soon(thread_id):
                await index.set_status(thread_id, "busy")

        return started


async def sync_thread(thread_id: str) -> None:
    """Index a thread as the local Agent Server has it now, or drop it once it is gone there."""
    await index.sync(thread_id, lambda: RUNTIMES.local.indexed(thread_id))


async def _sync_soon(thread_id: str) -> bool:
    """Sync a thread now, or, when that fails, from a job that retries; whether it synced now."""
    try:
        await sync_thread(thread_id)
        return True
    except Exception:
        logger.warning(
            "Copying a thread change to the index failed; retrying from a job",
            extra={"thread_id": thread_id},
            exc_info=True,
        )
    try:
        await queue.schedule(
            THREAD_SYNC_JOB, {"thread_id": thread_id}, key=f"{THREAD_SYNC_JOB}:{thread_id}"
        )
    except Exception:
        logger.error(
            "Could not queue a retry for a thread change; the index misses it",
            extra={"thread_id": thread_id},
            exc_info=True,
        )
    return False
