"""LangGraph entrypoint that polls open agent PRs for CI failures / conflicts.

A fallback for deployments where CI webhooks (``check_run`` / ``workflow_run``)
aren't reliably delivered, and the only path that can react to base-branch
merge conflicts (GitHub emits no webhook for those). Register it on a cron to
sweep periodically; each tick calls :func:`agent.ci_autofix.sweep_open_prs`.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import RunnableConfig

from .ci_autofix import sweep_open_prs

logger = logging.getLogger(__name__)


class CIMonitorState(TypedDict, total=False):
    result: dict[str, Any]


async def _sweep(_state: CIMonitorState, _config: RunnableConfig) -> dict[str, Any]:
    return {"result": await sweep_open_prs()}


def get_ci_monitor(config: RunnableConfig | None = None):
    builder = StateGraph(CIMonitorState)
    builder.add_node("sweep", _sweep)
    builder.add_edge(START, "sweep")
    builder.add_edge("sweep", END)
    return builder.compile().with_config(config or {})
