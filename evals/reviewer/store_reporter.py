"""Publish reviewer-eval progress to the LangGraph store for the dashboard.

When the eval runs in the ``Reviewer eval`` GitHub Action it writes the same
store record the dashboard reads (namespace ``["evals"]``, key ``"reviewer"``),
so ``/admin/evals`` shows the run live. The dashboard reconciles a run whose
heartbeat goes stale to ``failed`` (see ``agent.dashboard.eval_jobs``), so the
reporter must keep heartbeating while the eval runs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client

from agent.reviewer_eval_store import (
    _HEARTBEAT_INTERVAL_SECONDS,
    EVALS_NAMESPACE,
    REVIEWER_EVAL_KEY,
)

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """True when the eval should publish progress to the store (set by the Action)."""
    return bool(os.environ.get("REVIEWER_EVAL_REPORT_STORE")) and bool(
        os.environ.get("LANGGRAPH_URL")
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def github_run_url() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


class StoreReporter:
    """Writes the reviewer-eval status record + heartbeats to the deployment store."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        limit: int | None,
        total: int | None,
        created_by: str | None,
        completed_getter: Callable[[], int],
        tail_getter: Callable[[], str | None],
        experiment_url_getter: Callable[[], str | None],
    ) -> None:
        self._config = config
        self._limit = limit
        self._total = total
        self._created_by = created_by or os.environ.get("GITHUB_ACTOR")
        self._completed_getter = completed_getter
        self._tail_getter = tail_getter
        self._experiment_url_getter = experiment_url_getter
        self._github_run_url = github_run_url()
        self._worker_id = os.environ.get("GITHUB_RUN_ID")
        self._started_at = _now_iso()
        # get_client auto-loads the api key from LANGGRAPH/LANGSMITH/LANGCHAIN env.
        self._client = get_client(url=os.environ["LANGGRAPH_URL"])

    def _record(self, *, status: str, **overrides: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "name": REVIEWER_EVAL_KEY,
            "status": status,
            "run_name": self._config.get("experiment_prefix"),
            "langsmith_project": self._config.get("langsmith_project"),
            "limit": self._limit,
            "config_snapshot": self._config,
            "started_at": self._started_at,
            "finished_at": None,
            "created_by": self._created_by,
            "pid": None,
            "exit_code": None,
            "experiment_url": self._experiment_url_getter(),
            "error": None,
            "log_tail": self._tail_getter(),
            "worker_id": self._worker_id,
            "heartbeat": _now_iso(),
            "progress": {"completed": self._completed_getter(), "total": self._total},
            "github_run_url": self._github_run_url,
            "trigger": "github_action",
            "updated_at": _now_iso(),
        }
        record.update(overrides)
        return record

    async def _put(self, record: dict[str, Any]) -> None:
        try:
            await self._client.store.put_item(EVALS_NAMESPACE, REVIEWER_EVAL_KEY, record)
        except Exception:
            logger.warning("Failed to publish reviewer eval status to store", exc_info=True)

    async def start(self) -> None:
        await self._put(self._record(status="running"))

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            await self._put(self._record(status="running"))

    def run_heartbeat(self) -> asyncio.Task[None]:
        return asyncio.create_task(self._heartbeat_loop())

    async def finish(self, *, status: str, error: str | None = None) -> None:
        await self._put(self._record(status=status, finished_at=_now_iso(), error=error))
