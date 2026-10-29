"""Every kind of job, and the handler that runs it.

Importing this module registers the handlers on ``WORKER``; the webapp does so
before it starts the worker.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Final

from pydantic import JsonValue

from openswe import event_claims
from openswe.agent_api.report_receipts import PURGE_JOB as REPORT_RECEIPT_PURGE
from openswe.agent_api.report_receipts import PURGE_SCHEDULE as REPORT_RECEIPT_SCHEDULE
from openswe.agent_api.report_receipts import purge_report_receipts
from openswe.agent_api.tool_sets import PURGE_JOB as TOOL_SETS_PURGE
from openswe.agent_api.tool_sets import PURGE_SCHEDULE as TOOL_SETS_SCHEDULE
from openswe.agent_api.tool_sets import purge_tool_sets
from openswe.agent_cost import run_agent_cost_refresh
from openswe.announcements.delivery import ANNOUNCEMENT_TASK, deliver_announcement
from openswe.baby_sit import WATCH_JOB_KIND, evaluate_watch
from openswe.background_tasks import CRON_KIND as BACKGROUND_TASKS
from openswe.background_tasks import monitor_background_tasks
from openswe.human_review.standard import SCHEDULER_TASK as HUMAN_REVIEW_DEADLINE
from openswe.human_review.standard import run_deadline
from openswe.jobs import queue
from openswe.jobs.cron_import import import_agent_server_crons
from openswe.jobs.queue import Job
from openswe.jobs.worker import WORKER
from openswe.merge_queue.controller import start_queue
from openswe.merge_queue.runtime import JOB_KIND as MERGE_QUEUE_RECONCILE
from openswe.merge_queue.runtime import reconcile_queue
from openswe.monitors.delivery import POLL_JOB_KIND as MONITOR_POLL
from openswe.monitors.delivery import poll_monitors

__all__ = ["WORKER"]
from openswe.reconcile import reconcile_stale_runs
from openswe.sandboxes.retry import (
    SANDBOX_ATTACH_MAX_ELAPSED,
    is_transient_sandbox_error,
    retry_transient_sandbox_errors,
)
from openswe.schedules.store import AGENT_SCHEDULE_JOB_KIND, launch_scheduled_agent_run
from openswe.session_cost import run_session_cost_refresh
from openswe.slack.channel_options import DIRECTORY_JOB as SLACK_CHANNEL_DIRECTORY
from openswe.slack.channel_options import ensure_directory_sync, sync_directory
from openswe.threads.index_import import import_agent_server_threads
from openswe.threads.stalls import fail_stalled_runs
from openswe.threads.status import RECONCILE_JOB as THREAD_STATUS_RECONCILE
from openswe.threads.status import RECONCILE_SCHEDULE, reconcile_busy_threads
from openswe.tools.schedule_thread_wakeup import WAKE_THREAD_JOB, run_thread_wakeup
from openswe.workspaces.refresh import REFRESH_TASK as WORKSPACE_REFRESH
from openswe.workspaces.refresh import run_workspace_refresh_tick

logger = logging.getLogger(__name__)

AGENT_COST: Final = "agent_cost"
SESSION_COST: Final = "session_cost"
RECONCILE: Final = "reconcile"
_FIRE_CLAIM_TTL: Final = timedelta(days=2)

WORKER.register(MERGE_QUEUE_RECONCILE)(reconcile_queue)


def _text(job: Job, key: str) -> str | None:
    value = job.payload.get(key)
    return value if isinstance(value, str) and value else None


def _sandbox_work(
    kind: str,
) -> Callable[[Callable[[Job], Awaitable[object]]], Callable[[Job], Awaitable[object]]]:
    """Register a handler whose sandbox attaches ride out transient failures."""

    def decorate(run: Callable[[Job], Awaitable[object]]) -> Callable[[Job], Awaitable[object]]:
        @WORKER.register(kind)
        async def handle(job: Job) -> None:
            try:
                result = await retry_transient_sandbox_errors(
                    lambda: run(job),
                    description="Scheduled sandbox work",
                    max_elapsed=SANDBOX_ATTACH_MAX_ELAPSED,
                )
            except Exception as exc:
                if not is_transient_sandbox_error(exc):
                    raise
                logger.exception(
                    "Scheduled sandbox work exhausted transient retries",
                    extra={"job_kind": kind, "job_id": str(job.id)},
                )
                return
            logger.info(
                "Job ran", extra={"job_kind": kind, "job_id": str(job.id), "job_result": result}
            )

        return run

    return decorate


@_sandbox_work(BACKGROUND_TASKS)
async def _background_tasks(job: Job) -> dict[str, JsonValue] | None:
    thread_id = _text(job, "thread_id")
    return None if thread_id is None else await monitor_background_tasks(thread_id)


@_sandbox_work(MONITOR_POLL)
async def _monitors(job: Job) -> bool:
    thread_id = _text(job, "thread_id")
    polling = thread_id is not None and await poll_monitors(thread_id)
    if not polling:
        await job.stop()
    return polling


@_sandbox_work(WATCH_JOB_KIND)
async def _baby_sit(job: Job) -> str | None:
    key = _text(job, "watch_key")
    return None if key is None else await evaluate_watch(key)


@_sandbox_work(WORKSPACE_REFRESH)
async def _workspace_refresh(job: Job) -> object:
    kind = "update" if job.payload.get("refresh_kind") == "update" else "full"
    # A cron's id outlives each of its runs, so only a one-shot job names one refresh.
    run_id = None if job.cron else str(job.id)
    return await run_workspace_refresh_tick(_text(job, "workspace_slug"), kind, run_id=run_id)


@WORKER.register(AGENT_SCHEDULE_JOB_KIND)
async def _agent_schedule(job: Job) -> None:
    schedule_id = _text(job, "schedule_id")
    if schedule_id is None:
        return
    # Once per fire: a job claimed again after a deploy or crash may have launched it already.
    fire = f"{job.id}:{job.due_at.isoformat()}"
    if not await event_claims.claim(AGENT_SCHEDULE_JOB_KIND, fire, ttl=_FIRE_CLAIM_TTL):
        logger.info("Automation fire already launched", extra={"job_id": str(job.id)})
        return
    result = await launch_scheduled_agent_run(schedule_id, _text(job, "trigger_id"))
    logger.info(
        "Job ran", extra={"job_kind": job.kind, "job_id": str(job.id), "job_result": result}
    )


@_sandbox_work(SESSION_COST)
async def _session_cost(job: Job) -> object:
    return await run_session_cost_refresh(job.payload)


@_sandbox_work(AGENT_COST)
async def _agent_cost(job: Job) -> object:
    return await run_agent_cost_refresh(job.payload)


@_sandbox_work(ANNOUNCEMENT_TASK)
async def _announcement(job: Job) -> object:
    announcement_id = _text(job, "announcement_id")
    return None if announcement_id is None else await deliver_announcement(announcement_id)


@_sandbox_work(HUMAN_REVIEW_DEADLINE)
async def _human_review_deadline(job: Job) -> object:
    request_id, step = _text(job, "request_id"), _text(job, "step")
    if request_id is None or step is None:
        return None
    return await run_deadline(request_id, step)


@_sandbox_work(RECONCILE)
async def _reconcile(_job: Job) -> object:
    return await reconcile_stale_runs()


@_sandbox_work(THREAD_STATUS_RECONCILE)
async def _thread_status_reconcile(_job: Job) -> object:
    return {
        "threads_settled": await reconcile_busy_threads(),
        "stalled_runs_failed": await fail_stalled_runs(),
    }


@WORKER.register(SLACK_CHANNEL_DIRECTORY)
async def _slack_channel_directory(_job: Job) -> None:
    await sync_directory()


@WORKER.register(REPORT_RECEIPT_PURGE)
async def _report_receipt_purge(_job: Job) -> None:
    logger.info("Report receipts purged", extra={"purged": await purge_report_receipts()})


@WORKER.register(TOOL_SETS_PURGE)
async def _tool_sets_purge(_job: Job) -> None:
    logger.info("Tool sets purged", extra={"purged": await purge_tool_sets()})


@WORKER.register(WAKE_THREAD_JOB)
async def _wake_thread(job: Job) -> None:
    if job.lapsed_lease is not None:
        logger.warning(
            "Reclaimed thread wakeup skipped because its run may have started",
            extra={"job_id": str(job.id), "lapsed_lease": job.lapsed_lease.isoformat()},
        )
        return
    # Never retried: a run the server started before the client saw an error would start twice.
    try:
        run_id = await run_thread_wakeup(job.payload, job_id=str(job.id), due_at=job.due_at)
    except Exception:
        logger.exception("Thread wakeup failed", extra={"job_id": str(job.id)})
        return
    logger.info("Thread woken", extra={"job_id": str(job.id), "run_id": run_id})


async def start_jobs() -> None:
    """Startup work: move old scheduler crons over, and keep the standing jobs in place."""
    steps: list[Callable[[], Awaitable[object]]] = [
        import_agent_server_crons,
        import_agent_server_threads,
        start_queue,
        ensure_directory_sync,
        lambda: queue.schedule(
            THREAD_STATUS_RECONCILE, cron=RECONCILE_SCHEDULE, key=THREAD_STATUS_RECONCILE
        ),
        lambda: queue.schedule(
            REPORT_RECEIPT_PURGE, cron=REPORT_RECEIPT_SCHEDULE, key=REPORT_RECEIPT_PURGE
        ),
        lambda: queue.schedule(TOOL_SETS_PURGE, cron=TOOL_SETS_SCHEDULE, key=TOOL_SETS_PURGE),
    ]
    for step in steps:
        try:
            await step()
        except Exception:
            logger.exception("Job startup step failed")
