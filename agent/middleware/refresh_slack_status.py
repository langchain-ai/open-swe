"""Before-model middleware that refreshes the Slack typing indicator.

Slack's `assistants.threads.setStatus` indicator expires after ~2 minutes,
so on long agent runs it would silently disappear. Refreshing on every
model call keeps it visible while the agent is actively working — Slack
auto-clears it the moment the bot posts a real reply.

Gated behind `SLACK_ASSISTANTS_API_ENABLED` (checked inside
``set_slack_assistant_status``); a no-op when the flag is off or the run
has no associated Slack thread.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentState, before_model
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.slack import set_slack_assistant_status

logger = logging.getLogger(__name__)


@before_model
async def refresh_slack_assistant_status_before_model(
    state: AgentState,  # noqa: ARG001
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    try:
        config = get_config()
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        slack_thread = configurable.get("slack_thread") if isinstance(configurable, dict) else None
        if not isinstance(slack_thread, dict):
            return None

        channel_id = slack_thread.get("channel_id")
        thread_ts = slack_thread.get("thread_ts")
        if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
            return None
        if not channel_id or not thread_ts:
            return None

        await set_slack_assistant_status(channel_id, thread_ts)
    except Exception:
        logger.exception("Failed to refresh Slack assistant status")
    return None
