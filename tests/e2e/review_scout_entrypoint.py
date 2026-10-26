"""LangGraph review-scout entrypoint for the E2E dev server.

Mirrors ``agent_entrypoint``: applies the boundary patches in the backend worker,
then re-exports the read-only review scout graph.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import patches  # noqa: E402

patches.apply()

from openswe.graphs.review_scout import traced_review_scout  # noqa: E402

__all__ = ["traced_review_scout"]
