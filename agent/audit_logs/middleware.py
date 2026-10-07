"""Record authenticated dashboard write-method requests, never their payloads."""

from uuid import UUID

from fastapi.routing import APIRoute
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent.audit_logs.context import current_audit_log
from agent.audit_logs.models import AuditLog, AuditLogEnrichments
from agent.audit_logs.store import append_safely

_STATE_KEY = "audit_log"


def bind_actor(
    request: HTTPConnection,
    *,
    user_id: UUID | None = None,
    api_key_id: str | None = None,
    workspace_id: UUID | None = None,
    enrichments: AuditLogEnrichments,
) -> None:
    entry = getattr(request.state, _STATE_KEY, None)
    if isinstance(entry, AuditLog):
        entry.user_id = user_id
        entry.api_key_id = api_key_id
        entry.workspace_id = workspace_id
        entry.enrichments = enrichments


def _route_path(scope: Scope, route: APIRoute) -> str:
    fastapi_scope = scope.get("fastapi")
    if isinstance(fastapi_scope, dict):
        context = fastapi_scope.get("effective_route_context")
        path = getattr(context, "path", None)
        if getattr(context, "original_route", None) is route and isinstance(path, str):
            return path
    return route.path


class AuditLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return
        entry = AuditLog(operation_name="", operation_succeeded=False)
        scope.setdefault("state", {})[_STATE_KEY] = entry
        status: int | None = None

        async def audit_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        token = current_audit_log.set(entry)
        try:
            await self.app(scope, receive, audit_send)
            entry.operation_succeeded = status < 400 if status is not None else None
        finally:
            current_audit_log.reset(token)
            route = scope.get("route")
            if (
                isinstance(route, APIRoute)
                and (path := _route_path(scope, route)).startswith("/dashboard/api/")
                and entry.enrichments.actor_kind is not None
            ):
                entry.operation_name = route.name.removeprefix("api_")[:128]
                entry.enrichments.request_method = scope["method"]
                entry.enrichments.request_path = path
                entry.enrichments.response_status_code = status
                for value in scope.get("path_params", {}).values():
                    try:
                        resource_id = UUID(str(value))
                    except ValueError:
                        continue
                    entry.enrichments.resource_ids.append(str(resource_id))
                await append_safely(entry)
