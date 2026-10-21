"""CORS helpers for managed HTTP routes (``/connectors``, ``/channels``).

LangGraph applies ``CORS_ALLOW_ORIGINS`` to built-in Agent Server routes, but
GET responses from the custom ``http.app`` do not always receive those headers.
The managed app therefore adds CORS only on its own path prefixes so ``/ok``
and other built-ins are not double-headed.

``mda deploy`` still derives ``CORS_ALLOW_ORIGINS`` from
:data:`MDA_PUBLIC_APP_URL_ENV` plus optional :data:`MDA_CORS_ORIGINS_ENV`.

Subdomain policy: an allowlisted origin also permits **one** DNS label beneath
it (e.g. Cloudflare Pages previews under a project apex) **on the same scheme**.
Deeper nesting is rejected, and ``http://`` is never reflected for an
``https://`` allowlist entry (or the reverse). With ``allow_credentials=True``,
do not put hosts that issue attacker-reachable subdomains into
:data:`MDA_CORS_ORIGINS_ENV` / the public app URL — a malicious page on such a
subdomain could read credentialed ``/connectors`` and ``/channels`` responses.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from importlib import import_module
from typing import Any, Protocol, cast
from urllib.parse import urlparse

#: Comma-separated extra browser origins allowed for managed HTTP CORS.
#: Each entry also trusts a single subdomain label; avoid multi-tenant hosts.
MDA_CORS_ORIGINS_ENV = "MDA_CORS_ORIGINS"
MDA_PUBLIC_APP_URL_ENV = "MDA_PUBLIC_APP_URL"

MDA_CORS_ALLOW_HEADERS = (
    "Authorization",
    "Content-Type",
    "Accept",
    "X-Api-Key",
    "x-api-key",
)

MDA_CORS_ALLOW_METHODS = ("GET", "POST", "PUT", "DELETE", "OPTIONS")

#: Path prefixes that receive managed-app CORS (not built-in Agent Server routes).
MDA_CORS_PATH_PREFIXES = ("/connectors", "/channels")

__all__ = [
    "MDA_CORS_ALLOW_HEADERS",
    "MDA_CORS_ALLOW_METHODS",
    "MDA_CORS_ORIGINS_ENV",
    "MDA_CORS_PATH_PREFIXES",
    "AsgiApp",
    "is_managed_cors_path",
    "match_cors_origin",
    "resolve_cors_allowlist",
    "wrap_with_managed_cors",
]


class AsgiApp(Protocol):
    """Minimal ASGI callable (matches Starlette's ``ASGIApp``)."""

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
        /,
    ) -> None: ...


def resolve_cors_allowlist(
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Exact origins configured for CORS (trailing slashes stripped)."""
    source = os.environ if env is None else env
    origins: set[str] = set()
    public_app = source.get(MDA_PUBLIC_APP_URL_ENV, "").strip().rstrip("/")
    if public_app:
        origins.add(public_app)
    for part in source.get(MDA_CORS_ORIGINS_ENV, "").split(","):
        origin = part.strip().rstrip("/")
        if origin:
            origins.add(origin)
    return sorted(origins)


def _is_single_subdomain(request_host: str, allowed_host: str) -> bool:
    """True when ``request_host`` is exactly one DNS label under ``allowed_host``."""
    suffix = f".{allowed_host}"
    if not request_host.endswith(suffix):
        return False
    label = request_host[: -len(suffix)]
    return bool(label) and "." not in label


def match_cors_origin(request_origin: str, allowlist: list[str]) -> str | None:
    """Return ``request_origin`` when allowed, otherwise ``None``.

    Mirrors TypeScript ``matchCorsOrigin`` (exact origin, or one subdomain
    label on the same scheme). Starlette has no origin callback, so production
    uses :func:`_cors_origin_regex` instead; keep this helper for tests / policy
    parity.
    """
    parsed = urlparse(request_origin)
    if not parsed.scheme or not parsed.netloc:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in allowlist:
        return origin
    request_host = parsed.netloc
    for allowed in allowlist:
        allowed_parsed = urlparse(allowed)
        allowed_host = allowed_parsed.netloc
        if not allowed_host or parsed.scheme != allowed_parsed.scheme:
            continue
        if request_host == allowed_host or _is_single_subdomain(request_host, allowed_host):
            return origin
    return None


def is_managed_cors_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in MDA_CORS_PATH_PREFIXES)


def _cors_origin_regex(allowlist: list[str]) -> str | None:
    """Starlette ``allow_origin_regex`` approximating :func:`match_cors_origin`.

    Encodes exact hosts plus a single subdomain label (Starlette cannot call a
    Python predicate). Prefer :func:`match_cors_origin` when testing the policy.
    """
    parts: list[str] = []
    for allowed in allowlist:
        parsed = urlparse(allowed)
        if not parsed.scheme or not parsed.hostname:
            continue
        scheme = re.escape(parsed.scheme)
        if parsed.port:
            parts.append(rf"{scheme}://{re.escape(parsed.netloc)}")
            continue
        host = re.escape(parsed.hostname)
        parts.append(rf"{scheme}://{host}")
        # One label — matches :func:`_is_single_subdomain` in match_cors_origin.
        parts.append(rf"{scheme}://[a-zA-Z0-9-]+\.{host}")
    if not parts:
        return None
    return "|".join(f"(?:{part})" for part in parts)


def wrap_with_managed_cors(app: AsgiApp) -> AsgiApp:
    """Apply Starlette CORS only for managed path prefixes.

    Preserves a ``Starlette`` instance when given one so ``langgraph_api``'s
    ``load_custom_app`` isinstance check continues to pass (a bare ASGI
    callable / ``CORSMiddleware`` wrapper is rejected).
    """
    allowlist = resolve_cors_allowlist()
    if not allowlist:
        return app

    applications = import_module("starlette.applications")
    middleware_mod = import_module("starlette.middleware.cors")
    allow_methods = list(MDA_CORS_ALLOW_METHODS)
    allow_headers = list(MDA_CORS_ALLOW_HEADERS)
    allow_origin_regex = _cors_origin_regex(allowlist)

    class PathScopedCORSMiddleware:
        """Pure-ASGI middleware: CORSMiddleware only on managed path prefixes."""

        def __init__(self, inner: AsgiApp) -> None:
            self.app = inner
            # Pass kwargs explicitly so typecheckers can match CORSMiddleware's
            # typed parameters (a heterogeneous ``**dict`` fails ``ty``).
            self.cors_app = middleware_mod.CORSMiddleware(
                inner,
                allow_origins=allowlist,
                allow_origin_regex=allow_origin_regex,
                allow_credentials=True,
                allow_methods=allow_methods,
                allow_headers=allow_headers,
            )

        async def __call__(
            self,
            scope: MutableMapping[str, Any],
            receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
            send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
        ) -> None:
            path = str(scope.get("path", ""))
            root_path = str(scope.get("root_path", "")).rstrip("/")
            if root_path.startswith("/") and path.startswith(f"{root_path}/"):
                path = path[len(root_path) :]
            if scope.get("type") != "http" or not is_managed_cors_path(path):
                await self.app(scope, receive, send)
                return
            await self.cors_app(scope, receive, send)

    if isinstance(app, applications.Starlette):
        # Keep the Starlette type for langgraph_api custom-app loading.
        # Cast: Starlette's MiddlewareFactory typing is stricter than our ASGI Protocol.
        app.add_middleware(cast("Any", PathScopedCORSMiddleware))
        return app

    return PathScopedCORSMiddleware(app)
