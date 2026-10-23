"""What Managed Deep Agents runs call back into, behind the run token.

Every call is ``POST /agent-api/{endpoint}``, the typed API in
``runtime_api``: the graph's catalog, each phase of the run's model loop and the
work of its tools (see ``openswe.agent_api.graph``), and the launch of its
task workers (``openswe.agent_api.async_workers``). The middleware verifies
the bearer token once per request and leaves the verified run for the handler,
which picks the graph the token was signed for and reads the thread and run
configuration from it, never from the body.

An async subagent's first run carries a worker grant instead: it may read its
coordinator's catalog and ``adopt`` a run token of its own, nothing else.
"""

import logging
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Final

from langsmith import tracing_context
from langsmith.run_trees import RunTree
from pydantic import JsonValue, TypeAdapter, ValidationError
from runtime_api.catalog import CATALOG
from runtime_api.endpoint import API_PATH as _API_PATH
from runtime_api.endpoint import InvalidRequestError, UnknownEndpointError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openswe.agent_api.tokens import (
    RemoteRun,
    RuntimeTokenError,
    WorkerGrant,
    verify_runtime_token,
    verify_worker_grant,
)

logger = logging.getLogger(__name__)

API_PATH: Final = _API_PATH
_RUN_STATE_KEY: Final = "remote_run"
_request_body = TypeAdapter(dict[str, JsonValue])

Api = Callable[[RemoteRun, str, JsonValue], Awaitable[JsonValue]]


class RefusedCallError(PermissionError):
    """The run's token does not allow this call."""


def _graphs() -> Mapping[str, Api]:
    # The webapp must not import the agent stack at startup.
    from openswe.agent_api.hosted import hosted_graphs

    return {assistant_id: graph.api for assistant_id, graph in hosted_graphs().items()}


@contextmanager
def _traced_under_caller(scope: Scope) -> Iterator[None]:
    """Nest what the call traces under the runtime span that made it, when it says which.

    Without it every hook would start a trace of its own, apart from the run's turn.
    """
    headers = Request(scope).headers
    parent: RunTree | None = None
    if dotted_order := headers.get("langsmith-trace"):
        try:
            parent = RunTree.from_headers(
                {"langsmith-trace": dotted_order, "baggage": headers.get("baggage", "")}
            )
        except Exception:
            logger.warning("Ignoring unusable trace headers", exc_info=True)
    if parent is None:
        yield
        return
    with tracing_context(parent=parent):
        yield


def _scope_principal(scope: Scope) -> RemoteRun | WorkerGrant:
    principal = scope.get("state", {}).get(_RUN_STATE_KEY)
    if not isinstance(principal, RemoteRun | WorkerGrant):
        raise PermissionError("The call carried no verified run")
    return principal


async def _answer(principal: RemoteRun | WorkerGrant, endpoint: str, body: JsonValue) -> JsonValue:
    """What ``endpoint`` answers ``body`` with, as the run or worker grant that called it."""
    from openswe.agent_api import async_workers

    if isinstance(principal, WorkerGrant):
        if async_workers.GRANT_API.serves(endpoint):
            return await async_workers.GRANT_API.dispatch(endpoint, principal, body)
        if endpoint != CATALOG.name:
            raise RefusedCallError("A worker grant only adopts a run token")
        # Its tools, models and schemas only: the worker reads them before it has an identity.
        run = principal.coordinator
    else:
        run = principal
        if async_workers.RUN_API.serves(endpoint):
            return await async_workers.RUN_API.dispatch(endpoint, run, body)
    graph = _graphs().get(run.assistant_id)
    if graph is None:
        raise RefusedCallError(f"This backend serves no {run.assistant_id} graph")
    return await graph(run, endpoint, body)


async def api_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    from langgraph_api.logging import set_logging_context

    principal = _scope_principal(scope)
    endpoint = scope["path_params"]["endpoint"]
    raw = await Request(scope, receive).body()
    try:
        body = _request_body.validate_json(raw) if raw.strip() else {}
    except ValidationError:
        await JSONResponse({"detail": "The request body must be a JSON object"}, status_code=422)(
            scope, receive, send
        )
        return
    run = principal.coordinator if isinstance(principal, WorkerGrant) else principal
    context = {"thread_id": run.thread_id, "endpoint": endpoint}
    if isinstance(run_id := body.get("run_id"), str):
        context["run_id"] = run_id
    set_logging_context(context)
    try:
        with _traced_under_caller(scope):
            result = await _answer(principal, endpoint, body)
    except RefusedCallError as exc:
        await JSONResponse({"detail": str(exc)}, 403)(scope, receive, send)
        return
    except UnknownEndpointError:
        await JSONResponse({"detail": f"Unknown endpoint {endpoint}"}, status_code=404)(
            scope, receive, send
        )
        return
    except InvalidRequestError as exc:
        await JSONResponse({"detail": str(exc)}, status_code=422)(scope, receive, send)
        return
    except Exception as exc:
        logger.exception("Agent API call failed")
        # The run fails with what failed here, as it would have in-process.
        failure = {"error": type(exc).__name__, "message": str(exc)}
        await JSONResponse(failure, status_code=500)(scope, receive, send)
        return
    await JSONResponse(result)(scope, receive, send)


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
            principal = _verify(token.strip())
        except RuntimeTokenError as exc:
            logger.info("Agent API call refused", extra={"refusal": str(exc)})
            await _refuse(
                scope,
                receive,
                send,
                "The run token was not accepted",
                'Bearer error="invalid_token"',
            )
            return
        scope.setdefault("state", {})[_RUN_STATE_KEY] = principal
        await self.app(scope, receive, send)


def _verify(token: str) -> RemoteRun | WorkerGrant:
    try:
        return verify_runtime_token(token)
    except RuntimeTokenError as exc:
        if str(exc) != "InvalidAudienceError":
            raise
    return verify_worker_grant(token)


API: Final = RunTokenMiddleware(api_endpoint)
