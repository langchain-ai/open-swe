"""The single managed HTTP app (connector routes).

Mounted via ``langgraph.json`` ``http.app`` whenever the project opts into
managed identity or declares connectors. It layers onto the built-in LangGraph
routes:

* **connectors** — each connector gets a sub-router namespaced under
  ``/connectors/{name}``; ``events`` mounts the conventional public inbound
  route; ``http`` mounts additional secured/public routes.

Connector routes are **secure by default**: MDA resolves and enforces the
caller's identity before the handler runs, and a route must opt out of
authentication explicitly (``ctx.router.public.*``). The connector HTTP surface
is logged at construction so the (especially unauthenticated) routes are never
invisible.

Starlette is imported lazily so authoring-only installs stay light (it ships
with the deployed runtime).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

from managed_deepagents._channel_icon import channel_icon_headers, load_channel_icon
from managed_deepagents._channels.messaging_registry import (
    register_channel_messaging,
    register_connector_messaging,
)
from managed_deepagents._channels.source_providers import register_declared_source_providers
from managed_deepagents._cors import AsgiApp, wrap_with_managed_cors
from managed_deepagents._identity_auth import (
    HttpRequestLike,
    ManagedAuthError,
    resolve_request_identity,
)
from managed_deepagents._log import logger
from managed_deepagents.identity import (
    IdentityConfig,
    IdentityDefinitionLike,
    RuntimeIdentity,
)

EVENTS_MAX_BODY_BYTES = 1024 * 1024

RouteResult = object | Awaitable[object]
PublicRouteHandler = Callable[[HttpRequestLike], RouteResult]
SecuredRouteHandler = Callable[[HttpRequestLike, RuntimeIdentity], RouteResult]

if TYPE_CHECKING:
    from starlette.routing import BaseRoute as StarletteRoute

else:
    #: Starlette ``Route`` / ``Mount`` instances — ``object`` at runtime so
    #: Starlette stays a lazy import for authoring-only installs.
    StarletteRoute = object


class PublicSubRouter(Protocol):
    def get(self, path: str, handler: PublicRouteHandler, /) -> None: ...

    def post(self, path: str, handler: PublicRouteHandler, /) -> None: ...

    def put(self, path: str, handler: PublicRouteHandler, /) -> None: ...

    def delete(self, path: str, handler: PublicRouteHandler, /) -> None: ...


class HttpSubRouter(Protocol):
    public: PublicSubRouter

    def get(self, path: str, handler: SecuredRouteHandler, /) -> None: ...

    def post(self, path: str, handler: SecuredRouteHandler, /) -> None: ...

    def put(self, path: str, handler: SecuredRouteHandler, /) -> None: ...

    def delete(self, path: str, handler: SecuredRouteHandler, /) -> None: ...


class HttpContext(Protocol):
    router: HttpSubRouter
    identity: IdentityConfig | None

    async def require_identity(self, request: HttpRequestLike) -> RuntimeIdentity: ...


class HttpConnectorLike(Protocol):
    """Connector that may mount HTTP / Events routes."""

    kind: str
    name: str | None

    def http(self, ctx: HttpContext, /) -> None: ...


class MountedRoute(TypedDict, total=False):
    connector: str
    method: str
    path: str
    secured: bool
    #: Absolute path already (events / identity); skip ``/connectors/{name}`` prefix.
    absolute: bool


__all__ = ["build_managed_http_app"]


def build_managed_http_app(
    identity: IdentityDefinitionLike | IdentityConfig | None = None,
    connectors: Sequence[HttpConnectorLike | object] | None = None,
    channels: Sequence[object] | None = None,
    root: str | None = None,
) -> AsgiApp:
    """Build the managed HTTP app (a Starlette app) from identity + channels + connectors.

    Mounts channel Events under ``/channels/{name}/events``, each connector's
    ``http`` routes under ``/connectors/{name}``, and connector Events (e.g.
    GitHub) under ``/connectors/{name}/events``. A channel's ``icon``, read
    from the project at ``root``, is served at ``/channels/{name}/icon``.
    """
    applications = import_module("starlette.applications")

    cfg = _identity_config(identity)
    channel_list = list(channels or [])
    connector_list = list(connectors or [])
    register_declared_source_providers(channel_list)
    register_declared_source_providers(connector_list)
    register_channel_messaging(channel_list)
    register_connector_messaging(connector_list)

    routes: list[StarletteRoute] = []
    mounted: list[MountedRoute] = []

    for channel in channel_list:
        name = _channel_mount_name(channel)
        if callable(getattr(channel, "events", None)):
            routes.append(_channel_events_route(channel, name, mounted))
        icon = getattr(channel, "icon", None)
        if isinstance(icon, str) and icon:
            route = _channel_icon_route(icon, root, name, mounted)
            if route is not None:
                routes.append(route)

    for connector in connector_list:
        name = _connector_mount_name(connector)
        # Register the absolute Events route before the connector Mount so
        # Starlette does not swallow ``/connectors/{name}/events`` under the mount.
        if callable(getattr(connector, "events", None)):
            routes.append(_connector_events_route(connector, name, mounted))
        if callable(getattr(connector, "http", None)):
            routes.append(_connector_mount(connector, name, cfg, mounted))

    _log_connector_surface(mounted)

    # Path-scoped CORS for managed routes only. LangGraph covers built-ins via
    # CORS_ALLOW_ORIGINS, but GET responses from http.app often omit ACAO.
    app: AsgiApp = applications.Starlette(routes=routes)
    return wrap_with_managed_cors(app)


def _connector_mount_name(connector: object) -> str:
    name = getattr(connector, "name", None)
    if isinstance(name, str) and name.strip():
        return name
    kind = getattr(connector, "kind", None)
    if isinstance(kind, str) and kind.strip():
        # Unit tests often pass connectors without collect_connectors stamping.
        return kind
    raise ValueError(
        f'Connector kind "{kind}" has no file-stem name; '
        "pass connectors through collect_connectors before mounting."
    )


def _channel_mount_name(channel: object) -> str:
    name = getattr(channel, "name", None)
    if isinstance(name, str) and name.strip():
        return name
    kind = getattr(channel, "kind", None)
    if isinstance(kind, str) and kind.strip():
        return kind
    raise ValueError(
        f'Channel kind "{kind}" has no file-stem name; '
        "pass channels through collect_channels before mounting."
    )


def _channel_events_route(
    channel: object, name: str, mounted: list[MountedRoute]
) -> StarletteRoute:
    """Public Events route for a direct or Trigger-backed channel."""
    routing = import_module("starlette.routing")
    path = (
        f"/v1/channels/{name}/events"
        if getattr(channel, "transport", None) == "trigger_server"
        else f"/channels/{name}/events"
    )
    mounted.append(
        {
            "connector": f"channel:{name}",
            "method": "post",
            "path": path,
            "secured": False,
            "absolute": True,
        }
    )

    async def events_handler(request: HttpRequestLike) -> object:
        responses = import_module("starlette.responses")
        events = getattr(channel, "events", None)
        if not callable(events):
            raise TypeError(f'channel "{name}" is missing events')
        try:
            raw_body = await _read_limited_events_body(request)
            if raw_body is None:
                return responses.JSONResponse({"error": "payload too large"}, status_code=413)
            result = events(
                {
                    "name": name,
                    "request": request,
                    "raw_body": raw_body,
                    "channel": channel,
                }
            )
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as error:  # noqa: BLE001 - log a safe label, keep the HTTP body opaque
            logger.error("POST %s failed (%s)", path, logger.safe_error_label(error))
            return responses.JSONResponse({"error": "internal error"}, status_code=500)

    return cast("StarletteRoute", routing.Route(path, events_handler, methods=["POST"]))


def _channel_icon_route(
    file: str, root: str | None, name: str, mounted: list[MountedRoute]
) -> StarletteRoute | None:
    """Serve a channel's icon, read from the project once, at ``/channels/{name}/icon``.

    Public, so a provider can load it; an icon that can't be served is logged
    and left out.
    """
    path = f"/channels/{name}/icon"
    if not root:
        logger.warning("not serving %s: the project directory is unknown", path)
        return None
    try:
        icon = load_channel_icon(root, file)
    except (OSError, ValueError) as error:
        logger.warning("not serving %s (%s)", path, logger.safe_error_label(error))
        return None
    headers = channel_icon_headers(icon)
    mounted.append(
        {
            "connector": f"channel:{name}",
            "method": "get",
            "path": path,
            "secured": False,
            "absolute": True,
        }
    )
    routing = import_module("starlette.routing")

    async def icon_handler(_request: HttpRequestLike) -> object:
        responses = import_module("starlette.responses")
        return responses.Response(icon.data, headers=headers)

    return cast("StarletteRoute", routing.Route(path, icon_handler, methods=["GET"]))


async def _read_limited_events_body(request: HttpRequestLike) -> bytes | None:
    stream = getattr(request, "stream", None)
    if not callable(stream):
        body_method = getattr(request, "body", None)
        body = await body_method() if callable(body_method) else b""
        if isinstance(body, bytes) and len(body) <= EVENTS_MAX_BODY_BYTES:
            return body
        return None

    chunks: list[bytes] = []
    size = 0
    async for chunk in stream():
        size += len(chunk)
        if size > EVENTS_MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _connector_events_route(
    connector: object, name: str, mounted: list[MountedRoute]
) -> StarletteRoute:
    """Public Events route at ``POST /connectors/{name}/events``."""
    routing = import_module("starlette.routing")
    path = f"/connectors/{name}/events"
    mounted.append(
        {
            "connector": name,
            "method": "post",
            "path": path,
            "secured": False,
            "absolute": True,
        }
    )

    async def events_handler(request: HttpRequestLike) -> object:
        responses = import_module("starlette.responses")
        events = getattr(connector, "events", None)
        if not callable(events):
            raise TypeError(f'connector "{name}" is missing events')
        try:
            raw_body = await _read_limited_events_body(request)
            if raw_body is None:
                return responses.JSONResponse({"error": "payload too large"}, status_code=413)
            result = events(
                {
                    "name": name,
                    "request": request,
                    "raw_body": raw_body,
                    "connector": connector,
                }
            )
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as error:  # noqa: BLE001 - log a safe label, keep the HTTP body opaque
            logger.error("POST %s failed (%s)", path, logger.safe_error_label(error))
            return responses.JSONResponse({"error": "internal error"}, status_code=500)

    return cast("StarletteRoute", routing.Route(path, events_handler, methods=["POST"]))


def _connector_mount(
    connector: object,
    name: str,
    cfg: IdentityConfig | None,
    mounted: list[MountedRoute],
) -> StarletteRoute:
    """Mount a connector's ``http`` routes under ``/connectors/{name}``."""
    routing = import_module("starlette.routing")

    http_connector = cast("HttpConnectorLike", connector)
    router = _ConnectorRouter(name, cfg, mounted)
    ctx = _HttpContext(router=router, cfg=cfg)
    http_connector.http(ctx)
    return cast("StarletteRoute", routing.Mount(f"/connectors/{name}", routes=router.routes))


class _HttpContext(HttpContext):
    """Request context handed to a connector's ``http`` hook."""

    def __init__(self, router: HttpSubRouter, cfg: IdentityConfig | None) -> None:
        self.router = router
        self.identity = cfg

    async def require_identity(self, request: HttpRequestLike) -> RuntimeIdentity:
        """Resolve caller identity (or raise). For use inside public routes."""
        if self.identity is None:
            raise RuntimeError(
                "identity is not configured for this deployment; declare an "
                "identity to resolve caller identity on connector routes."
            )
        return await resolve_request_identity(self.identity, request)


class _ConnectorRouter(HttpSubRouter):
    """Secure-by-default router the connector registers routes on.

    Routes registered directly resolve identity (401 on failure) before the
    handler runs and pass the frozen identity as the handler's second argument.
    ``public.*`` routes skip enforcement.
    """

    def __init__(self, name: str, cfg: IdentityConfig | None, mounted: list[MountedRoute]) -> None:
        self._name = name
        self._cfg = cfg
        self._mounted = mounted
        self.routes: list[StarletteRoute] = []
        self.public = _PublicRouter(name, self.routes, mounted)

    def _add(self, method: str, path: str, handler: SecuredRouteHandler) -> None:
        routing = import_module("starlette.routing")
        self._mounted.append(
            {"connector": self._name, "method": method, "path": path, "secured": True}
        )
        self.routes.append(
            routing.Route(path, _secured_endpoint(handler, self._cfg), methods=[method])
        )

    def get(self, path: str, handler: SecuredRouteHandler) -> None:
        self._add("GET", path, handler)

    def post(self, path: str, handler: SecuredRouteHandler) -> None:
        self._add("POST", path, handler)

    def put(self, path: str, handler: SecuredRouteHandler) -> None:
        self._add("PUT", path, handler)

    def delete(self, path: str, handler: SecuredRouteHandler) -> None:
        self._add("DELETE", path, handler)


class _PublicRouter(PublicSubRouter):
    """Explicit opt-out: routes reachable without MDA identity enforcement."""

    def __init__(
        self, name: str, routes: list[StarletteRoute], mounted: list[MountedRoute]
    ) -> None:
        self._name = name
        self._routes = routes
        self._mounted = mounted

    def _add(self, method: str, path: str, handler: PublicRouteHandler) -> None:
        routing = import_module("starlette.routing")
        self._mounted.append(
            {"connector": self._name, "method": method, "path": path, "secured": False}
        )
        self._routes.append(routing.Route(path, handler, methods=[method]))

    def get(self, path: str, handler: PublicRouteHandler) -> None:
        self._add("GET", path, handler)

    def post(self, path: str, handler: PublicRouteHandler) -> None:
        self._add("POST", path, handler)

    def put(self, path: str, handler: PublicRouteHandler) -> None:
        self._add("PUT", path, handler)

    def delete(self, path: str, handler: PublicRouteHandler) -> None:
        self._add("DELETE", path, handler)


def _secured_endpoint(
    handler: SecuredRouteHandler, cfg: IdentityConfig | None
) -> PublicRouteHandler:
    """Wrap a connector handler so identity is resolved and enforced first."""

    async def endpoint(request: HttpRequestLike) -> object:
        if cfg is None:
            return _unauthorized(
                401,
                "secured connector route requires an identity declaration, but "
                "this deployment has none",
            )
        try:
            identity = await resolve_request_identity(cfg, request)
        except ManagedAuthError as err:
            return _unauthorized(err.status, err.detail)
        result = handler(request, identity)
        if inspect.isawaitable(result):
            result = await result
        return result

    return endpoint


def _unauthorized(status: int, detail: str) -> object:
    responses = import_module("starlette.responses")
    return responses.JSONResponse({"error": detail}, status_code=status)


def _log_connector_surface(mounted: list[MountedRoute]) -> None:
    """Log the managed HTTP surface so public routes are never invisible."""
    if not mounted:
        return
    logger.info("managed HTTP surface (%s route(s)):", len(mounted))
    for route in mounted:
        connector = str(route["connector"])
        if route.get("absolute") or connector.startswith("identity:"):
            full = route["path"]
        else:
            full = f"/connectors/{connector}{route['path']}"
        line = f"  {route['method'].upper():<6} {full}"
        if route["secured"]:
            logger.info("%s  (identity enforced)", line)
        else:
            logger.warning("%s  (PUBLIC — no identity enforcement)", line)


def _identity_config(
    identity: IdentityDefinitionLike | IdentityConfig | None,
) -> IdentityConfig | None:
    if identity is None:
        return None
    if isinstance(identity, Mapping):
        return cast("IdentityConfig", identity)
    return identity.config
