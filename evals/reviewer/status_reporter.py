"""Report reviewer-eval progress to the dashboard.

When the eval runs in a LangSmith sandbox (``openswe.review.eval_jobs``) it posts
its progress to the deployment that launched it, so ``/admin/evals`` shows the
run live. The dashboard reconciles a run whose reports stop to ``failed`` (see
``openswe.review.eval_jobs``), so the reporter must keep reporting while the
eval runs.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Literal

import httpx2

from openswe.review.eval_report import (
    ERROR_CHARS,
    HEARTBEAT_INTERVAL_SECONDS,
    REPORT_URL_ENV,
    ReviewerEvalReport,
)

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """True when the launcher asked the eval to report its progress."""
    return bool(os.environ.get(REPORT_URL_ENV))


class StatusReporter:
    """Posts the eval's progress, as its heartbeat, to the deployment that launched it."""

    def __init__(
        self,
        *,
        total: int | None,
        completed_getter: Callable[[], int],
        tail_getter: Callable[[], str | None],
        experiment_url_getter: Callable[[], str | None],
    ) -> None:
        self._total = total
        self._completed_getter = completed_getter
        self._tail_getter = tail_getter
        self._experiment_url_getter = experiment_url_getter
        self._url = os.environ[REPORT_URL_ENV]

    def _report(
        self, status: Literal["running", "completed", "failed"], error: str | None = None
    ) -> ReviewerEvalReport:
        return ReviewerEvalReport(
            status=status,
            completed_examples=self._completed_getter(),
            total_examples=self._total,
            experiment_url=self._experiment_url_getter(),
            log_tail=self._tail_getter(),
            error=None if error is None else error[:ERROR_CHARS],
        )

    async def _post(self, report: ReviewerEvalReport) -> None:
        try:
            async with httpx2.AsyncClient(timeout=30) as client:
                response = await client.post(self._url, json=report.model_dump(mode="json"))
                response.raise_for_status()
        except Exception:
            logger.warning("Failed to report reviewer eval status", exc_info=True)

    async def start(self) -> None:
        await self._post(self._report("running"))

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            await self._post(self._report("running"))

    def run_heartbeat(self) -> asyncio.Task[None]:
        return asyncio.create_task(self._heartbeat_loop())

    async def finish(
        self, *, status: Literal["completed", "failed"], error: str | None = None
    ) -> None:
        await self._post(self._report(status, error))
