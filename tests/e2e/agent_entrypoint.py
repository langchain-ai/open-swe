"""LangGraph graph entrypoint for the E2E dev server.

Applies the boundary patches (fake LLM + fake GitHub/Slack), then re-exports the
REAL traced agent factory. The langgraph dev config points the ``agent`` graph
here instead of ``agent.server`` so the patches are in effect in the worker.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import patches  # noqa: E402

patches.apply()

from agent.server import traced_agent  # noqa: E402

__all__ = ["traced_agent"]
