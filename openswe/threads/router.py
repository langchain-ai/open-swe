"""Serve the Agent Server's thread API from the PostgreSQL thread index.

``ThreadRouter`` sits in front of the core ``/threads`` and ``/runs`` routes, so
every client (the dashboard, the CLI and the backend's own ``client.threads``)
keeps working unchanged:

- Creating, reading, updating, deleting, searching and counting threads is
  answered from the index; each change is copied to the runtimes that keep the
  thread, so its runs and traces see it.
- A new thread is created on Managed Deep Agents (``mda``). A thread's runs,
  state, history, commands and streams go to its runtime: MDA, or for ``local``
  threads made before that, this deployment's Agent Server, which logs a
  warning for each. Local threads are read-only: starting a run, cron or
  posting command there, or copying one, is refused. A run started on MDA
  marks the thread busy; the run-complete webhook and a reconcile job settle it.
- A thread the index does not know yet is read from the Agent Server, indexed,
  and served from then on.
"""

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Final, Literal
from urllib.parse import parse_qs

from pydantic import JsonValue, TypeAdapter, ValidationError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from openswe.api.agent_server_auth import backend_call
from openswe.remote_runtime.asgi import (
    StatusRecorder,
    forward,
    json_body,
    read_body,
    replay,
    send_json,
)
from openswe.remote_runtime.client import (
    MDA_GRAPH_ID,
    MDA_RUNTIME,
    RUNTIME_METADATA_KEY,
)
from openswe.threads import index
from openswe.threads.index import IndexedThread, ThreadExistsError, ThreadSearch
from openswe.threads.runtimes import LOCAL_HEADER, RUNTIMES

logger = logging.getLogger(__name__)

_object = TypeAdapter(dict[str, JsonValue])

_THREADS: Final = re.compile(r"^(?:/noauth)?/threads/?$")
_SEARCH: Final = re.compile(r"^(?:/noauth)?/threads/search$")
_COUNT: Final = re.compile(r"^(?:/noauth)?/threads/count$")
_THREAD: Final = re.compile(r"^(?:/noauth)?/threads/(?P<thread_id>[0-9a-fA-F-]{36})(?P<rest>/.*)?$")
_CANCEL_MANY: Final = re.compile(r"^(?:/noauth)?/runs/cancel$")
_CREATE_RUN: Final = re.compile(r"^/runs(?:/stream|/wait)?$")
_CREATE_CRON: Final = "/runs/crons"
# Commands that add to a thread rather than read or stop it.
POSTING_COMMANDS: Final = frozenset({"run.start", "input.respond", "input.inject", "state.fork"})


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


def _warn_local(scope: Scope, thread: IndexedThread) -> None:
    """Only threads made before every new one started on MDA are still ``local``."""
    logger.warning(
        "Served a thread that is not on Managed Deep Agents",
        extra={
            "thread_id": thread.thread_id,
            "thread_runtime": thread.runtime,
            "thread_status": thread.status,
            "thread_created_at": thread.created_at.isoformat(),
            "thread_updated_at": thread.updated_at.isoformat(),
            "thread_metadata": thread.metadata,
            "graph_id": thread.metadata.get("graph_id"),
            "http_method": scope["method"],
            "http_path": scope["path"],
            "query_string": bytes(scope.get("query_string", b"")).decode("latin-1"),
            "user_agent": Headers(scope=scope).get("user-agent"),
        },
    )


async def _refuse_local(scope: Scope, send: Send, thread: IndexedThread) -> None:
    _warn_local(scope, thread)
    # Not 409, which callers read as "a run is already going" and retry or skip.
    await send_json(
        send,
        410,
        {
            "detail": "This thread predates Managed Deep Agents and is read-only. "
            "Start a new thread to continue."
        },
    )


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
            await self._search(scope, send, body, count=False)
        elif _COUNT.match(path):
            await self._search(scope, send, body, count=True)
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
                await self._copy(scope, send, thread_id)
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
            thread_id,
            runtime="mda",
            metadata={**metadata, RUNTIME_METADATA_KEY: MDA_RUNTIME},
            if_exists=if_exists,
        )
        await RUNTIMES.local.create(thread_id, thread.metadata)
        await RUNTIMES.mda.create(thread_id, _for_mda(thread.metadata))
        return thread

    async def _thread_itself(
        self, scope: Scope, receive: Receive, send: Send, body: bytes, thread_id: str, method: str
    ) -> None:
        thread = await self._thread(thread_id)
        if thread is None:
            await _not_found(send, thread_id)
            return
        if thread.runtime == "local":
            _warn_local(scope, thread)
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

    async def _search(self, scope: Scope, send: Send, body: bytes, *, count: bool) -> None:
        # The SDK sends only the Agent Server's fields in the body, so ``text`` rides the URL.
        text = parse_qs(scope["query_string"].decode()).get("text", [None])[-1]
        try:
            query = ThreadSearch.model_validate({**(json_body(body) or {}), "text": text})
        except ValidationError as exc:
            await send_json(send, 422, {"detail": str(exc)})
            return
        if count:
            await send_json(send, 200, await query.count())
            return
        await send_json(
            send, 200, [thread.as_langgraph(query.select) for thread in await query.run()]
        )

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
        if thread.runtime == "local":
            await _refuse_local(scope, send, thread)
            return
        # Busy before the answer reaches the caller, so a run that ends at once
        # is not marked busy after its run-complete webhook settled it.
        recorder = StatusRecorder(send, on_success=None if cron else _marks_busy(thread_id))
        mda = RUNTIMES.mda_runtime
        await mda.ensure_remote_thread(thread_id)
        rewritten = await mda.run_payload(thread_id, payload)
        await forward(mda.http, scope, recorder, json.dumps(rewritten).encode())

    async def _command(
        self, scope: Scope, receive: Receive, send: Send, body: bytes, thread_id: str
    ) -> None:
        command = json_body(body)
        thread = await self._thread(thread_id)
        if command is None or thread is None:
            await self._to_runtime(scope, receive, send, body, thread_id)
            return
        method = command.get("method")
        if thread.runtime == "local" and method in POSTING_COMMANDS:
            await _refuse_local(scope, send, thread)
            return
        params = command.get("params")
        if method == "run.start" and isinstance(params, dict):
            reply = await RUNTIMES.mda_runtime.start_command_run(thread_id, command, params)
            if reply.get("type") == "success":
                await index.set_status(thread_id, "busy")
            await send_json(send, 200, reply)
            return
        await self._to_runtime(scope, receive, send, body, thread_id)

    async def _copy(self, scope: Scope, send: Send, thread_id: str) -> None:
        """Copy a thread on its runtime, and index the copy as the source is."""
        thread = await self._thread(thread_id)
        if thread is None:
            await _not_found(send, thread_id)
            return
        if thread.runtime == "local":
            await _refuse_local(scope, send, thread)
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
        await RUNTIMES.local.create(copy_id, copied.metadata)
        await send_json(send, 200, copied.as_langgraph())

    async def _to_runtime(
        self, scope: Scope, receive: Receive, send: Send, body: bytes, thread_id: str
    ) -> None:
        thread = await self._thread(thread_id)
        if thread is not None and thread.runtime == "mda":
            mda = RUNTIMES.mda_runtime
            await mda.ensure_remote_thread(thread_id)
            await forward(mda.http, scope, send, body)
            return
        if thread is not None:
            _warn_local(scope, thread)
        await self.app(scope, receive, send)

    async def _cancel_many(self, scope: Scope, receive: Receive, send: Send, body: bytes) -> None:
        payload = json_body(body) or {}
        thread_id = payload.get("thread_id")
        if isinstance(thread_id, str):
            await self._to_runtime(scope, receive, send, body, thread_id)
            return
        await self.app(scope, receive, send)
