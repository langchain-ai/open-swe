"""LangGraph graph entrypoint for the reviewer in the E2E dev server.

Same shape as ``agent_entrypoint``: apply the boundary patches, then re-export
the REAL reviewer factory. Registering it lets a spec prove the reviewer would
have posted a review on a PR — which is what makes the stand-down on Open SWE's
own PRs a real assertion instead of an absence nobody tested.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import patches  # noqa: E402

patches.apply()

from agent.reviewer import traced_reviewer_agent  # noqa: E402

__all__ = ["traced_reviewer_agent"]
