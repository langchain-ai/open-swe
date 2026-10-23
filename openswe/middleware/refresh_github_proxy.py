"""Keeps the sandbox GitHub proxy token fresh while the run works.

The LangSmith sandbox proxy is configured with a GitHub App installation token
that expires after exactly one hour. Long runs would otherwise hit 401s on
every ``gh``/``git`` call once that snapshot goes stale. The runtime asks for a
refresh before each model call; it re-configures the proxy with a fresh token
only when the recorded one is near expiry.
"""

import logging

from langchain.agents.middleware import AgentState
from langgraph.config import get_config

from openswe.github.proxy import maybe_refresh_proxy_token
from openswe.middleware.trace import OpenSWEMiddleware

logger = logging.getLogger(__name__)


class GitHubAccessRefresh(OpenSWEMiddleware[AgentState]):
    async def arefresh_github_access(self) -> None:
        """Refresh the sandbox proxy's GitHub token before it expires mid-run."""
        try:
            thread_id = get_config().get("configurable", {}).get("thread_id")
        except Exception:  # noqa: BLE001
            return
        if not thread_id:
            return
        try:
            await maybe_refresh_proxy_token(thread_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to refresh GitHub proxy token for thread %s", thread_id, exc_info=True
            )


github_access_refresh = GitHubAccessRefresh()
