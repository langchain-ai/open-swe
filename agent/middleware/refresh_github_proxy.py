"""Before-model middleware that keeps the sandbox GitHub proxy token fresh.

The LangSmith sandbox proxy is configured with a GitHub App installation token
that expires after exactly one hour. Long runs would otherwise hit 401s on
every ``gh``/``git`` call once that snapshot goes stale. This hook re-configures
the proxy with a fresh token before each model call when the recorded token is
near expiry.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentState, before_model
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.github_proxy import maybe_refresh_proxy_token

logger = logging.getLogger(__name__)


@before_model
async def refresh_github_proxy_before_model(
    state: AgentState,  # noqa: ARG001
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    """Refresh the sandbox proxy's GitHub token before it expires mid-run."""
    try:
        config = get_config()
        thread_id = config.get("configurable", {}).get("thread_id")
    except Exception:  # noqa: BLE001
        return None

    if not thread_id:
        return None

    try:
        await maybe_refresh_proxy_token(thread_id)
        import os
        if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
            from ..utils.github_token import get_github_token
            from ..utils.github_proxy import configure_sandbox_github_auth
            from ..utils.sandbox_state import SANDBOX_BACKENDS
            token = get_github_token()
            if token:
                sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
                if sandbox_backend:
                    await configure_sandbox_github_auth(sandbox_backend, token)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to refresh GitHub proxy token for thread %s",
            thread_id,
            exc_info=True,
        )
    return None
