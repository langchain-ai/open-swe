"""Web backend: OAuth, profiles, and admin endpoints for the open-swe UI.

``router`` is loaded lazily (PEP 562): importing any web submodule
(e.g. ``openswe.web.options`` from middleware) executes this __init__,
and it must NOT drag in routes.py + FastAPI + every API/job module. Only the
webapp, which actually mounts the router, pays that cost.
"""

from typing import TYPE_CHECKING, Any

__all__ = ["router"]

if TYPE_CHECKING:
    from openswe.web.routes import router


def __getattr__(name: str) -> Any:
    if name == "router":
        from openswe.web.routes import router

        globals()[name] = router
        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
