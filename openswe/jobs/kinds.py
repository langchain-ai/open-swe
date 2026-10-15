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

__all__ = ["WORKER"]
from openswe.reconcile import reconcile_stale_runs
from openswe.sandboxes.retry import (
    SANDBOX_ATTACH_MAX_ELAPSED,
    is_transient_sandbox_error,
    retry_transient_sandbox_errors,
)
from openswe.schedules.store import AGENT_SCHEDULE_JOB_KIND, launch_scheduled_agent_run
from openswe.session_cost import run_session_cost_refresh
from openswe.threads.index import index_kept
from openswe.threads.index_import import import_agent_server_threads, mark_index_stale
from openswe.threads.router import THREAD_SYNC_JOB, sync_thread
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


@_sandbox_work(WATCH_JOB_KIND)
async def _baby_sit(job: Job) -> str | None:
    key = _text(job, "watch_key")
    return None if key is None else await evaluate_watch(key)


@_sandbox_work(WORKSPACE_REFRESH)
async def _workspace_refresh(job: Job) -> object:
    kind = "update" if job.payload.get("refresh_kind") == "update" else "full"
    return await run_workspace_refresh_tick(_text(job, "workspace_slug"), kind)


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
    return await reconcile_busy_threads()


@WORKER.register(WAKE_THREAD_JOB)
async def _wake_thread(job: Job) -> None:
    # Never retried: a run the server started before the client saw an error would start twice.
    try:
        run_id = await run_thread_wakeup(job.payload)
    except Exception:
        logger.exception("Thread wakeup failed", extra={"job_id": str(job.id)})
        return
    logger.info("Thread woken", extra={"job_id": str(job.id), "run_id": run_id})


@WORKER.register(THREAD_SYNC_JOB)
async def _thread_index_sync(job: Job) -> None:
    thread_id = _text(job, "thread_id")
    if thread_id is not None:
        await sync_thread(thread_id)


async def start_jobs() -> None:
    """Startup work: move old scheduler crons over, and keep the standing jobs in place."""
    steps: list[Callable[[], Awaitable[object]]] = [import_agent_server_crons]
    if index_kept():
        steps += [
            import_agent_server_threads,
            lambda: queue.schedule(
                THREAD_STATUS_RECONCILE, cron=RECONCILE_SCHEDULE, key=THREAD_STATUS_RECONCILE
            ),
        ]
    else:
        steps += [mark_index_stale, lambda: queue.cancel_key(THREAD_STATUS_RECONCILE)]
    for step in steps:
        try:
            await step()
        except Exception:
            logger.exception("Job startup step failed")
