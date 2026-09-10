"""LangGraph scheduler entrypoint for the E2E dev server.

Mirrors ``agent_entrypoint``: applies the boundary patches, then re-exports the
REAL scheduler factory. Declared so the E2E runs the same topology production
does — an environment refresh is a background run on this graph, so a config
without it would silently never refresh anything.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import patches  # noqa: E402

patches.apply()

from agent.scheduler import get_scheduler  # noqa: E402

__all__ = ["get_scheduler"]
