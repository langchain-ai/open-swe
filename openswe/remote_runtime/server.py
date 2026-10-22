"""What Managed Deep Agents runs call back into, behind the run token.

Every call is ``POST /remote-runtime/hooks/{hook}`` with a JSON body: the graph's
catalog, each phase of the run's model loop, and its tool calls (see
``openswe.remote_runtime.graph``). The middleware verifies the bearer token once
per request and leaves what it proves for the handler. The handler takes the run's
configuration from the body's ``run_config`` only when it matches the hash the
token signed, then picks the graph the token was signed for.
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
    RunTicket,
    RuntimeTokenError,
    verify_runtime_token,
)

logger = logging.getLogger(__name__)

HOOKS_PATH: Final = "/remote-runtime/hooks/{hook}"
_TICKET_STATE_KEY: Final = "run_ticket"
# The body key carrying the run's configuration, as the text the token's hash covers.
RUN_CONFIG_KEY: Final = "run_config"
_hook_body = TypeAdapter(dict[str, JsonValue])


class UnknownHookError(LookupError):
    """A remote graph called a hook this backend does not serve."""


Hook = Callable[[RemoteRun, str, Mapping[str, JsonValue]], Awaitable[JsonValue]]


def _graphs() -> Mapping[str, Hook]:
    # The webapp must not import the agent stack at startup.
    from openswe.remote_runtime.hosted import hosted_graphs

    return {assistant_id: graph.hook for assistant_id, graph in hosted_graphs().items()}


def _scope_ticket(scope: Scope) -> RunTicket:
    ticket = scope.get("state", {}).get(_TICKET_STATE_KEY)
    if not isinstance(ticket, RunTicket):
        raise PermissionError("The call carried no verified run")
    return ticket


async def hook_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    ticket = _scope_ticket(scope)
    hook = scope["path_params"]["hook"]
    graph = _graphs().get(ticket.assistant_id)
    if graph is None:
        await JSONResponse({"detail": f"No remote runtime serves {ticket.assistant_id}"}, 403)(
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
    config_text = body.pop(RUN_CONFIG_KEY, None)
    try:
        run = ticket.run(config_text if isinstance(config_text, str) else None)
    except RuntimeTokenError as exc:
        logger.info("Remote runtime call refused", extra={"refusal": str(exc)})
        await JSONResponse({"detail": "The run configuration was not accepted"}, 403)(
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
    """Verify the run token on every request and leave what it proves for the handler."""

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
            ticket = verify_runtime_token(token.strip())
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
        scope.setdefault("state", {})[_TICKET_STATE_KEY] = ticket
        await self.app(scope, receive, send)


HOOKS: Final = RunTokenMiddleware(hook_endpoint)
