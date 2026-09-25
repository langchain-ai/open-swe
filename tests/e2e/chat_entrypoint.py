"""LangGraph review-chat entrypoint for the E2E dev server.

Mirrors ``agent_entrypoint``: applies the boundary patches, then re-exports the
REAL chat graph the review page's chat panel streams from.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import patches  # noqa: E402

patches.apply()

from agent.chat import traced_chat_agent  # noqa: E402

__all__ = ["traced_chat_agent"]
