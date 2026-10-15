"""What Managed Deep Agents runs call back into, behind the run token.

Every call is ``POST /remote-runtime/hooks/{hook}`` with a JSON body: the graph's
catalog, each phase of the run's model loop, and its tool calls (see
``openswe.remote_runtime.graph``). The middleware verifies the bearer token once
per request and leaves the verified run for the handler, which picks the graph
the token was signed for and reads the thread and run configuration from it,
never from the body.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from functools import cache
from typing import Final

from pydantic import JsonValue, TypeAdapter, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openswe.remote_runtime.tokens import RemoteRun, RuntimeTokenError, verify_runtime_token

logger = logging.getLogger(__name__)

HOOKS_PATH: Final = "/remote-runtime/hooks/{hook}"
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
        result = await graph(run, hook, body)
    except UnknownHookError:
        await JSONResponse({"detail": f"Unknown hook {hook}"}, status_code=404)(
            scope, receive, send
        )
        return
    except Exception:
        logger.exception(
            "Remote runtime hook failed", extra={"hook": hook, "thread_id": run.thread_id}
        )
        raise
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
