"""What Managed Deep Agents runs call back into, behind the run token.

Every call is ``POST /remote-runtime/hooks/{hook}`` with a JSON body: the graph's
catalog, each phase of the run's model loop, and its tool calls (see
``openswe.remote_runtime.graph``). The middleware verifies the bearer token once
per request and leaves the verified run for the handler, which picks the graph
the token was signed for and reads the thread and run configuration from it,
never from the body.

An async subagent's first run carries a worker grant instead: it may read its
coordinator's catalog and ``adopt`` a run token of its own, nothing else.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from pydantic import JsonValue, TypeAdapter, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openswe.remote_runtime.tokens import (
    RemoteRun,
    RuntimeTokenError,
    WorkerGrant,
    verify_runtime_token,
    verify_worker_grant,
)

logger = logging.getLogger(__name__)

HOOKS_PATH: Final = "/remote-runtime/hooks/{hook}"
_RUN_STATE_KEY: Final = "remote_run"
_hook_body = TypeAdapter(dict[str, JsonValue])


class UnknownHookError(LookupError):
    """A remote graph called a hook this backend does not serve."""


Hook = Callable[[RemoteRun, str, Mapping[str, JsonValue]], Awaitable[JsonValue]]
RunHook = Callable[[RemoteRun, Mapping[str, JsonValue]], Awaitable[JsonValue]]
GrantHook = Callable[[WorkerGrant, Mapping[str, JsonValue]], Awaitable[JsonValue]]


def _graphs() -> Mapping[str, Hook]:
    # The webapp must not import the agent stack at startup.
    from openswe.remote_runtime.hosted import hosted_graphs

    return {assistant_id: graph.hook for assistant_id, graph in hosted_graphs().items()}


def _run_hooks() -> Mapping[str, RunHook]:
    """Hooks a run calls that belong to no graph phase: launching async workers."""
    from openswe.remote_runtime import async_workers

    return {"launch_worker": async_workers.launch, "worker_launched": async_workers.launched}


def _grant_hooks() -> Mapping[str, GrantHook]:
    from openswe.remote_runtime import async_workers

    return {"adopt": async_workers.adopt}


def _scope_principal(scope: Scope) -> RemoteRun | WorkerGrant:
    principal = scope.get("state", {}).get(_RUN_STATE_KEY)
    if not isinstance(principal, RemoteRun | WorkerGrant):
        raise PermissionError("The call carried no verified run")
    return principal


async def hook_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    principal = _scope_principal(scope)
    hook = scope["path_params"]["hook"]
    grant_hook = _grant_hooks().get(hook) if isinstance(principal, WorkerGrant) else None
    if isinstance(principal, WorkerGrant):
        if grant_hook is None and hook != "catalog":
            await JSONResponse({"detail": "A worker grant only adopts a run token"}, 403)(
                scope, receive, send
            )
            return
        # Its tools, models and schemas only: the worker reads them before it has an identity.
        run = principal.coordinator
    else:
        run = principal
    run_hook = _run_hooks().get(hook) if isinstance(principal, RemoteRun) else None
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
        if isinstance(principal, WorkerGrant) and grant_hook is not None:
            result = await grant_hook(principal, body)
        elif run_hook is not None:
            result = await run_hook(run, body)
        else:
            result = await graph(run, hook, body)
    except UnknownHookError:
        await JSONResponse({"detail": f"Unknown hook {hook}"}, status_code=404)(
            scope, receive, send
        )
        return
    except Exception as exc:
        logger.exception(
            "Remote runtime hook failed", extra={"hook": hook, "thread_id": run.thread_id}
        )
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
            logger.info("Remote runtime call refused", extra={"refusal": str(exc)})
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


HOOKS: Final = RunTokenMiddleware(hook_endpoint)
