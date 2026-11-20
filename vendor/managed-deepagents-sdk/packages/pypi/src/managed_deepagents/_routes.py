"""Authored ``routes``: Deep Agents backends mounted at path prefixes."""

from __future__ import annotations

import re
from collections.abc import Mapping

from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol

#: Prefixes MDA mounts itself; authored routes may not shadow or nest in them.
RESERVED_ROUTE_PREFIXES = ("/instructions.md/", "/skills/", "/memories/")

_ROUTE_PREFIX = re.compile(r"(/[A-Za-z0-9_][A-Za-z0-9._-]*)+/\Z")

Routes = Mapping[str, BackendProtocol]


def snapshot_routes(value: object) -> dict[str, BackendProtocol]:
    """Validate authored routes and return a copy."""
    if not isinstance(value, Mapping):
        msg = "routes must be a mapping of path prefixes to Deep Agents backends"
        raise TypeError(msg)
    routes: dict[str, BackendProtocol] = {}
    for prefix, backend in value.items():
        if not isinstance(prefix, str) or not _ROUTE_PREFIX.match(prefix):
            msg = (
                f"routes key {prefix!r} must be an absolute directory prefix such as "
                '"/documents/": start and end with "/", with no "." or ".." segments'
            )
            raise ValueError(msg)
        if reserved := next(
            (r for r in RESERVED_ROUTE_PREFIXES if prefix.startswith(r) or r.startswith(prefix)),
            None,
        ):
            msg = f"routes key {prefix!r} overlaps {reserved!r}, which MDA mounts itself"
            raise ValueError(msg)
        if not isinstance(backend, BackendProtocol):
            msg = f"routes[{prefix!r}] must be a Deep Agents backend (BackendProtocol)"
            raise TypeError(msg)
        routes[prefix] = backend
    return routes


def mount_routes(backend: BackendProtocol | None, routes: Routes) -> BackendProtocol:
    """Add authored routes to the managed backend; unmatched paths keep it.

    Routes join an existing composite rather than nesting one, because a nested
    composite hides the sandbox default from ``execute``.
    """
    if isinstance(backend, CompositeBackend):
        return type(backend)(
            backend.default,
            {**backend.routes, **routes},
            artifacts_root=backend.artifacts_root,
        )
    return CompositeBackend(
        default=backend if backend is not None else StateBackend(),
        routes=dict(routes),
    )
