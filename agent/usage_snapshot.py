"""Out-of-process builder graph for the usage-tab snapshots.

A single pure-python node (no LLM, no sandbox) that rebuilds every period's
usage leaderboard + reviewer-stats snapshot and writes them to the cache
namespaces. Scheduled by a global cron (see ``dashboard/usage_snapshot_cron.py``)
so heavy/looping work never runs on the run-serving HTTP process (the #1434 bug
class). The whole build is bounded by ``asyncio.timeout`` so a wedged build
self-cancels before the next tick.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import RunnableConfig

from .dashboard.agent_usage import (
    refresh_reviewer_stats_cache,
    refresh_usage_leaderboard_cache,
)

logger = logging.getLogger(__name__)

_PERIODS = ("7d", "30d", "all")
_BUILD_TIMEOUT_S = 120


class UsageSnapshotState(TypedDict, total=False):
    result: dict[str, Any]


def _cron_enabled() -> bool:
    value = os.environ.get("USAGE_SNAPSHOT_CRON_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


async def _build(state: UsageSnapshotState, config: RunnableConfig) -> dict[str, Any]:
    if not _cron_enabled():
        logger.info("Usage snapshot build skipped: USAGE_SNAPSHOT_CRON_ENABLED is off")
        return {"result": {"status": "disabled"}}

    built: list[str] = []
    failed: list[str] = []
    try:
        async with asyncio.timeout(_BUILD_TIMEOUT_S):
            for period in _PERIODS:
                for label, refresh in (
                    ("usage", refresh_usage_leaderboard_cache),
                    ("reviewer", refresh_reviewer_stats_cache),
                ):
                    try:
                        await refresh(period)
                        built.append(f"{label}:{period}")
                    except Exception:
                        logger.exception("Usage snapshot build failed for %s:%s", label, period)
                        failed.append(f"{label}:{period}")
    except TimeoutError:
        logger.warning("Usage snapshot build exceeded %ss budget; cancelled", _BUILD_TIMEOUT_S)
        return {"result": {"status": "timeout", "built": built, "failed": failed}}

    status = "ok" if not failed else "partial"
    logger.info("Usage snapshot build %s: built=%s failed=%s", status, built, failed)
    return {"result": {"status": status, "built": built, "failed": failed}}


def get_usage_snapshot(config: RunnableConfig | None = None):
    builder = StateGraph(UsageSnapshotState)
    builder.add_node("build", _build)
    builder.add_edge(START, "build")
    builder.add_edge("build", END)
    return builder.compile().with_config(config or {})
