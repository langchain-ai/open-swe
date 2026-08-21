"""The web layer for the open-swe UI: OAuth, HTTP endpoints, thread APIs.

What the dashboard *stores* lives in :mod:`agent.settings`; this package only
speaks HTTP. A few in-graph modules still call the thread/plan/approval APIs
directly (``agent.tools.threads``, ``agent.webhooks.slack``), so ``router`` is
still loaded lazily (PEP 562): importing one of those submodules executes this
__init__, and it must NOT drag in the ``routes`` package + FastAPI + every API
module. Only the webapp, which actually mounts the router, pays that cost.
"""

from typing import TYPE_CHECKING, Any

__all__ = ["router"]

if TYPE_CHECKING:
    from .routes import router


def __getattr__(name: str) -> Any:
    if name == "router":
        from .routes import router

        globals()[name] = router
        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
