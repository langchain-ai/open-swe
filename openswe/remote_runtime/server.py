"""What Managed Deep Agents runs call back into, behind the run token.

A run calls hooks with a JSON body: the graph's catalog, each phase of the run's
model loop, and its tool calls (see ``openswe.remote_runtime.graph``). It holds one
websocket at ``/remote-runtime/session`` for the whole run, so every call reaches
the replica that built the run's graph and connected its sandbox; ``POST
/remote-runtime/hooks/{hook}`` answers single calls on any replica. The run token
is verified once per request or connection, and the handler picks the graph the
token was signed for and reads the thread and run configuration from it, never
from the body.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from functools import cache
from typing import Final

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect

from openswe.remote_runtime.tokens import RemoteRun, RuntimeTokenError, verify_runtime_token

logger = logging.getLogger(__name__)

HOOKS_PATH: Final = "/remote-runtime/hooks/{hook}"
SESSION_PATH: Final = "/remote-runtime/session"
# Policy violation: the close code for a session whose token is refused.
_REFUSED: Final = 1008
_RUN_STATE_KEY: Final = "remote_run"
_hook_body = TypeAdapter(dict[str, JsonValue])


class UnknownHookError(LookupError):
    """A remote graph called a hook this backend does not serve."""


Hook = Callable[[RemoteRun, str, Mapping[str, JsonValue]], Awaitable[JsonValue]]


@cache
def _graphs() -> Mapping[str, Hook]:
    # The webapp must not import the agent stack at startup.
    from openswe.remote_runtime.agent import AGENT
    from openswe.remote_runtime.reviewer import REVIEWER

    return {graph.assistant_id: graph.hook for graph in (AGENT, REVIEWER)}


def _scope_run(scope: Scope) -> RemoteRun:
    run = scope.get("state", {}).get(_RUN_STATE_KEY)
    if not isinstance(run, RemoteRun):
        raise PermissionError("The call carried no verified run")
    return run


async def hook_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    run = _scope_run(scope)
    hook = scope["path_params"]["hook"]
    graph = _graphs().get(run.assistant_id)
    if graph is None:
        await JSONResponse({"detail": f"No remote runtime serves {run.assistant_id}"}, 403)(
            scope, receive, send
        )
        return
    raw = await Request(scope, receive).body()
    try:
        body = _hook_body.validate_json(raw) if raw.strip() else {}
    except ValidationError:
        await JSONResponse({"detail": "The hook body must be a JSON object"}, status_code=422)(
            scope, receive, send
        )
        return
    try:
        result = await _answer(graph, run, hook, body)
    except UnknownHookError:
        await JSONResponse({"detail": f"Unknown hook {hook}"}, status_code=404)(
            scope, receive, send
        )
        return
    await JSONResponse(result)(scope, receive, send)


async def _answer(
    graph: Hook, run: RemoteRun, hook: str, body: Mapping[str, JsonValue]
) -> JsonValue:
    try:
        return await graph(run, hook, body)
    except UnknownHookError:
        raise
    except Exception:
        logger.exception(
            "Remote runtime hook failed", extra={"hook": hook, "thread_id": run.thread_id}
        )
        raise


class SessionCall(BaseModel):
    """One hook call on a run's session; its reply carries the same id."""

    id: int
    hook: str
    body: dict[str, JsonValue] = {}


class HookSession:
    """A run's websocket: its hook calls, answered concurrently on this replica."""

    def __init__(self, websocket: WebSocket, run: RemoteRun, graph: Hook) -> None:
        self._websocket = websocket
        self._run = run
        self._graph = graph
        self._sending = asyncio.Lock()
        self._calls: set[asyncio.Task[None]] = set()

    async def serve(self) -> None:
        try:
            while True:
                raw = await self._websocket.receive_text()
                try:
                    call = SessionCall.model_validate_json(raw)
                except ValidationError:
                    logger.warning(
                        "Remote runtime session call refused",
                        extra={"thread_id": self._run.thread_id},
                    )
                    continue
                task = asyncio.create_task(self._reply(call))
                self._calls.add(task)
                task.add_done_callback(self._calls.discard)
        except WebSocketDisconnect:
            # Calls already running finish, as a dropped HTTP request's would, and
            # a shutting-down server waits for them while this handler does.
            if self._calls:
                await asyncio.gather(*self._calls, return_exceptions=True)

    async def _reply(self, call: SessionCall) -> None:
        reply: dict[str, JsonValue]
        try:
            result = await _answer(self._graph, self._run, call.hook, call.body)
            reply = {"id": call.id, "result": result}
        except UnknownHookError:
            reply = {"id": call.id, "error": {"status": 404, "detail": f"Unknown hook {call.hook}"}}
        except Exception:
            # `_answer` logged it; the run sees the failure as an HTTP call's 500.
            reply = {"id": call.id, "error": {"status": 500, "detail": "The hook failed"}}
        try:
            async with self._sending:
                await self._websocket.send_json(reply)
        except WebSocketDisconnect, RuntimeError:
            logger.warning(
                "Remote runtime session closed before its reply",
                extra={"thread_id": self._run.thread_id, "hook": call.hook},
            )


async def session_endpoint(websocket: WebSocket) -> None:
    scheme, _, token = websocket.headers.get("authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not token.strip():
            raise RuntimeTokenError("no bearer token")
        run = verify_runtime_token(token.strip())
    except RuntimeTokenError as exc:
        logger.info("Remote runtime session refused", extra={"refusal": str(exc)})
        await websocket.close(code=_REFUSED)
        return
    graph = _graphs().get(run.assistant_id)
    if graph is None:
        logger.info("Remote runtime session refused", extra={"refusal": "unserved graph"})
        await websocket.close(code=_REFUSED)
        return
    await websocket.accept()
    await HookSession(websocket, run, graph).serve()


async def _refuse(scope: Scope, receive: Receive, send: Send, detail: str, challenge: str) -> None:
    response = JSONResponse(
        {"detail": detail}, status_code=401, headers={"WWW-Authenticate": challenge}
    )
    await response(scope, receive, send)


class RunTokenMiddleware:
    """Verify the run token on every request and leave the verified run for the handler."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scheme, _, token = Request(scope).headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            await _refuse(scope, receive, send, "A run token is required", "Bearer")
            return
        try:
            run = verify_runtime_token(token.strip())
        except RuntimeTokenError as exc:
            logger.info("Remote runtime call refused", extra={"refusal": str(exc)})
            await _refuse(
                scope,
                receive,
                send,
                "The run token was not accepted",
                'Bearer error="invalid_token"',
            )
            return
        scope.setdefault("state", {})[_RUN_STATE_KEY] = run
        await self.app(scope, receive, send)


HOOKS: Final = RunTokenMiddleware(hook_endpoint)
