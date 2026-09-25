"""LangGraph review-scout entrypoint for the E2E dev server.

Mirrors ``agent_entrypoint``: applies the boundary patches, then re-exports the
REAL review scout graph the review page's "Build walkthrough" button starts.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import patches  # noqa: E402

patches.apply()

from agent.review_scout.graph import traced_review_scout  # noqa: E402

__all__ = ["traced_review_scout"]
