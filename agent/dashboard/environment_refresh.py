"""Rebuilding an environment's snapshot from its scripts.

Two kinds of refresh, both on a throwaway builder sandbox that is stopped once
its capture lands:

- ``full``: boot from the base snapshot, run ``setup_script`` then
  ``update_script``, capture. Nightly per environment on a LangGraph cron, and on
  demand from an admin thread. This is the lineage reset — every update chains
  snapshot to snapshot, and the nightly rebuild from base keeps drift from
  accumulating.
- ``update``: boot from the environment's *current* snapshot, run only
  ``update_script`` (a ``git pull``, a dependency sync), capture. Triggered
  lazily: creating a sandbox for an environment whose snapshot is older than
  ``UPDATE_INTERVAL_SECONDS`` enqueues one in the background, so the image
  converges and later creations skip the work.

Creating a sandbox from a stale snapshot *also* runs the update script in that
sandbox, before the first model call — see ``SandboxCreateConfig.run_update_script``.
The background capture alone is not enough: it never helps the run that
triggered it, and when runs are sparse every run is a triggering run, so the
first one after a quiet spell would work against a checkout as old as the last
nightly rebuild.

The outcome — status, kind, timestamps, a capped log — lands on the environment
record for the dashboard. A failed refresh of either kind leaves the previous
snapshot in place: runs keep booting from the last image that worked.
"""

import hashlib
import logging
import shlex
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client

from agent.config import ENV, EnvVar
from agent.dashboard.environments import (
    ENVIRONMENTS,
    Environment,
    RefreshKind,
    capture_environment_snapshot,
    require_capture_support,
    script_command,
    script_log_path,
)
from agent.dashboard.sandbox_settings import resolve_base_snapshot_id

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "scheduler"
REFRESH_TASK = "environment_refresh"

DEFAULT_SCRIPT_TIMEOUT_SECONDS = 30 * 60
DEFAULT_UPDATE_TIMEOUT_SECONDS = 10 * 60
DEFAULT_CAPTURE_TIMEOUT_SECONDS = 30 * 60
# How stale a snapshot may get, while in use, before the next sandbox creation
# kicks off an update on a builder.
UPDATE_INTERVAL_SECONDS = 60 * 60
# A refresh that has been "refreshing" for longer than this lost its worker
# (redeploy, crash) and must not block the next attempt forever.
STALE_REFRESH_SECONDS = 3 * 60 * 60
# The builder is stopped as soon as its capture lands and nobody reconnects to it.
BUILDER_DELETE_AFTER_STOP_SECONDS = 10 * 60


def _seconds(var: EnvVar, default: int) -> int:
    """A positive timeout from ``var``, else ``default``."""
    seconds = var.get_int(default)
    return seconds if seconds > 0 else default


def capture_timeout() -> int:
    return _seconds(ENV.ENVIRONMENT_CAPTURE_TIMEOUT_SECONDS, DEFAULT_CAPTURE_TIMEOUT_SECONDS)


def _client():
    return get_client()


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def is_refresh_in_flight(record: Environment) -> bool:
    """Whether another refresh of this environment is still plausibly running."""
    if record.refresh_status != "refreshing":
        return False
    started_at = _parse_iso(record.refresh_started_at)
    if started_at is None:
        return False
    return (datetime.now(UTC) - started_at).total_seconds() < STALE_REFRESH_SECONDS


def daily_schedule(slug: str) -> str:
    """Daily cron expression, staggered per environment to spread the rebuilds."""
    digest = int(hashlib.sha256(slug.encode()).hexdigest(), 16)
    return f"{digest % 60} {3 + (digest // 60) % 3} * * *"  # 03:00–05:59 UTC


async def ensure_refresh_cron(slug: str) -> str | None:
    """Idempotently register the daily refresh cron for an environment."""
    record = await ENVIRONMENTS.get(slug)
    if record is None:
        return None
    if record.refresh_cron_id:
        return record.refresh_cron_id
    try:
        cron = await _client().crons.create(
            _ASSISTANT_ID,
            schedule=daily_schedule(slug),
            input={"task": REFRESH_TASK, "environment_slug": slug},
            metadata={"kind": REFRESH_TASK, "environment": slug},
        )
    except Exception:
        logger.exception("Failed to create refresh cron for environment %s", slug)
        return None
    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    if not (isinstance(cron_id, str) and cron_id):
        return None
    record.refresh_cron_id = cron_id
    await ENVIRONMENTS.save(record)
    return cron_id


async def remove_refresh_cron(record: Environment | None) -> None:
    """Delete the refresh cron carried by an environment record, if any."""
    if record is None or not record.refresh_cron_id:
        return
    try:
        await _client().crons.delete(record.refresh_cron_id)
    except Exception:
        logger.debug("Could not delete refresh cron %s", record.refresh_cron_id, exc_info=True)


async def _release_builder_sandbox(sandbox_id: str) -> None:
    """Stop the builder so the platform reclaims it on its delete-after-stop window.

    Nothing here deletes a sandbox: an id-keyed delete is how a running box with
    uncommitted work gets destroyed, so reclamation stays the platform's job.
    """
    from agent.sandboxes.providers.langsmith import get_async_sandbox_client

    try:
        async with get_async_sandbox_client() as client:
            await client.stop_sandbox(sandbox_id)
    except Exception:
        logger.warning("Failed to stop builder sandbox %s", sandbox_id, exc_info=True)


async def _create_builder_sandbox(record: Environment, snapshot_id: str | None) -> Any:
    from agent.github.app import get_github_app_installation_token
    from agent.sandboxes.providers.langsmith import create_langsmith_sandbox

    token = await get_github_app_installation_token()
    if not token:
        raise RuntimeError("GitHub App installation token is unavailable")
    return await create_langsmith_sandbox(
        github_token=token,
        environment_slug=record.slug,
        snapshot_id=snapshot_id,
        create_params={
            **record.sandbox_create_params(),
            "delete_after_stop_seconds": BUILDER_DELETE_AFTER_STOP_SECONDS,
        },
        **record.sandbox_resources(),
    )


def _scripts_to_run(record: Environment, kind: RefreshKind) -> list[tuple[str, str, int]]:
    """The scripts a refresh runs, in order, as ``(label, command, timeout)``.

    A full rebuild runs the update script too, so a broken one is caught nightly
    on a builder rather than discovered by the next hourly update.
    """
    steps: list[tuple[str, str, int]] = []
    if kind == "full":
        steps.append(
            (
                "setup",
                script_command(record.setup_script, "setup"),
                _seconds(ENV.ENVIRONMENT_REFRESH_TIMEOUT_SECONDS, DEFAULT_SCRIPT_TIMEOUT_SECONDS),
            )
        )
    if record.update_script:
        steps.append(
            (
                "update",
                script_command(record.update_script, "update"),
                _seconds(ENV.ENVIRONMENT_UPDATE_TIMEOUT_SECONDS, DEFAULT_UPDATE_TIMEOUT_SECONDS),
            )
        )
    return steps


def is_snapshot_stale(record: Environment) -> bool:
    """Whether the image a new sandbox boots from has aged past the interval.

    This gates the update that runs *in the run's own sandbox*, so it keys on
    when the snapshot was captured — not on when a refresh was last attempted.
    Every sandbox gets its own copy of the snapshot, so while the image is stale
    each one needs the script; once a capture lands, they all boot fresh.
    """
    if not record.update_script or record.ready_snapshot_id is None:
        return False
    captured = _parse_iso(record.last_captured_at)
    if captured is None:
        return True
    return (datetime.now(UTC) - captured).total_seconds() >= UPDATE_INTERVAL_SECONDS


def is_update_due(record: Environment) -> bool:
    """Whether a new sandbox should also kick off a background snapshot update.

    Same staleness test, plus: no refresh already running, and the last attempt
    — success *or* failure — is older than the interval. Counting failures keeps
    a broken script or network from enqueueing a builder on every creation.
    """
    if not is_snapshot_stale(record):
        return False
    if is_refresh_in_flight(record):
        return False
    finished = _parse_iso(record.refresh_finished_at)
    if finished is None:
        return True
    return (datetime.now(UTC) - finished).total_seconds() >= UPDATE_INTERVAL_SECONDS


async def maybe_start_update(record: Environment | None) -> str | None:
    """Start a background update for ``record`` if one is due; never raises."""
    if record is None or not is_update_due(record):
        return None
    try:
        return await start_refresh_run(record.slug, kind="update")
    except Exception:
        logger.warning("Could not start environment update for %s", record.slug, exc_info=True)
        return None


async def refresh_environment(slug: str, kind: RefreshKind = "full") -> dict[str, Any]:
    """Refresh ``slug``'s snapshot on a throwaway builder.

    ``full`` boots from the base snapshot and runs setup then update; ``update``
    boots from the current snapshot and runs only the update script. Either way
    the capture happens only if every script exited 0.

    Returns a status dict rather than raising, carrying the combined log: this is
    awaited by an admin thread iterating on a script, by a cron tick, and by a
    background run, and none of them should surface a traceback. The same detail
    lands on the environment record.
    """
    record = await ENVIRONMENTS.get(slug)
    if record is None:
        return {"status": "unknown_environment", "slug": slug}
    if kind == "full" and not record.setup_script:
        return {"status": "no_setup_script", "slug": slug}
    if kind == "update" and not record.update_script:
        return {"status": "no_update_script", "slug": slug}
    if kind == "update" and record.ready_snapshot_id is None:
        return {"status": "no_snapshot_to_update", "slug": slug}
    if is_refresh_in_flight(record):
        return {"status": "already_refreshing", "slug": slug}
    try:
        require_capture_support()
    except RuntimeError as exc:
        return {"status": "unsupported", "slug": slug, "error": str(exc)}

    base = (
        record.ready_snapshot_id
        if kind == "update"
        else record.base_snapshot_id or await resolve_base_snapshot_id()
    )
    await ENVIRONMENTS.mark_refreshing(slug, kind)
    started = datetime.now(UTC)
    sandbox_id: str | None = None
    log = ""
    try:
        await ENVIRONMENTS.start_refresh_step(slug, "boot")
        backend = await _create_builder_sandbox(record, base)
        sandbox_id = str(backend.id)
        await ENVIRONMENTS.finish_refresh_step(slug, "boot", "success")
        # Published before the first script so a poll can tail the trace while it
        # runs; cleared when the refresh settles and the builder is released.
        await ENVIRONMENTS.mark_refresh_builder(slug, sandbox_id)
        # Every script runs before the capture, so a snapshot only ships once the
        # whole of what produced it worked.
        for label, command, timeout in _scripts_to_run(record, kind):
            await ENVIRONMENTS.start_refresh_step(slug, label, log_path=script_log_path(label))
            result = await backend.aexecute(command, timeout=timeout)
            log = f"{log}\n--- {label} script ---\n{result.output or ''}".strip()
            if result.exit_code != 0:
                await ENVIRONMENTS.finish_refresh_step(
                    slug, label, "failed", exit_code=result.exit_code
                )
                error = f"{label} script exited {result.exit_code}"
                await ENVIRONMENTS.mark_refresh_settled(slug, "failed", log=log, error=error)
                return {
                    "status": "failed",
                    "slug": slug,
                    "script": label,
                    "exit_code": result.exit_code,
                    "error": error,
                    "log": log,
                }
            await ENVIRONMENTS.finish_refresh_step(slug, label, "success", exit_code=0)
        await ENVIRONMENTS.start_refresh_step(slug, "capture")
        await capture_environment_snapshot(slug, sandbox_id, timeout=capture_timeout())
        await ENVIRONMENTS.finish_refresh_step(slug, "capture", "success")
    except Exception as exc:
        logger.warning("Refresh failed for environment %s", slug, exc_info=True)
        await ENVIRONMENTS.mark_refresh_settled(slug, "failed", log=log, error=str(exc))
        return {"status": "failed", "slug": slug, "error": str(exc), "log": log}
    finally:
        if sandbox_id:
            await _release_builder_sandbox(sandbox_id)

    elapsed = int((datetime.now(UTC) - started).total_seconds())
    await ENVIRONMENTS.mark_refresh_settled(slug, "success", log=log)
    logger.info("Refreshed environment %s (%s) in %ss", slug, kind, elapsed)
    return {"status": "success", "slug": slug, "kind": kind, "seconds": elapsed, "log": log}


async def start_refresh_run(slug: str, kind: RefreshKind = "full") -> str | None:
    """Kick off a refresh as its own background run and return its id.

    Nothing awaits a rebuild in-line: an HTTP request, a sandbox being created
    and an admin tool call all get the run id back and read progress through
    ``background_task``.
    """
    try:
        run = await _client().runs.create(
            None,
            _ASSISTANT_ID,
            input={"task": REFRESH_TASK, "environment_slug": slug, "refresh_kind": kind},
            metadata={"kind": REFRESH_TASK, "environment": slug, "refresh_kind": kind},
            on_completion="delete",
        )
    except Exception:
        logger.exception("Failed to start refresh run for environment %s", slug)
        return None
    run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
    if not isinstance(run_id, str):
        return None
    # Recorded so a poll can tell this refresh from a later one that superseded it.
    record = await ENVIRONMENTS.get(slug)
    if record is not None:
        record.refresh_run_id = run_id
        await ENVIRONMENTS.save(record)
    return run_id


TASK_PREFIX = "env"
TASK_KIND = "environment_refresh"
# A trace tail, not the whole log: enough to see the step in flight.
LIVE_LOG_MAX_BYTES = 4_000
LIVE_LOG_READ_TIMEOUT_SECONDS = 20

_TASK_STATUS = {
    "refreshing": "running",
    "success": "completed",
    "failed": "failed",
}


def refresh_task_id(run_id: str) -> str:
    """The unified background-task id for a refresh run."""
    return f"{TASK_PREFIX}-{run_id}"


def owns_task(task_id: str) -> bool:
    return task_id.startswith(f"{TASK_PREFIX}-")


async def _record_for_task(task_id: str) -> Environment | None:
    """The environment whose *current* refresh this task id names.

    Keyed on the run id rather than the slug so a handle from a superseded
    refresh resolves to nothing instead of reporting a later run's progress.
    """
    run_id = task_id.removeprefix(f"{TASK_PREFIX}-")
    for record in await ENVIRONMENTS.list_all():
        if record.refresh_run_id == run_id:
            return record
    return None


async def _read_builder_log(sandbox_id: str, path: str) -> str | None:
    """Tail a script's live ``bash -x`` trace off the builder, or ``None``.

    Best-effort by design: the builder is released the moment the capture lands,
    so an unreachable box means "no live trace", never a failed poll. Goes
    through the provider registry rather than the LangSmith client so every
    provider — the local one the E2E runs on included — takes this same path.
    """
    from agent.sandboxes.providers.registry import create_sandbox

    try:
        backend = await create_sandbox(sandbox_id=sandbox_id)
        result = await backend.aexecute(
            f"tail -c {LIVE_LOG_MAX_BYTES} {shlex.quote(path)} 2>/dev/null",
            timeout=LIVE_LOG_READ_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.debug("Builder sandbox %s is not readable", sandbox_id, exc_info=True)
        return None
    return (result.output or "").strip() or None


def _running_step(record: Environment) -> Any:
    return next((step for step in record.refresh_steps if step.status == "running"), None)


async def task_status(task_id: str, *, with_output: bool = True) -> dict[str, Any]:
    """One refresh, in the shape ``background_task`` reports every task in."""
    record = await _record_for_task(task_id)
    if record is None:
        return {
            "error": "task not found",
            "task_id": task_id,
            "detail": (
                "no environment is tracking this refresh — it finished long enough ago "
                "to be superseded by a later one, or never started"
            ),
        }
    step = _running_step(record)
    state: dict[str, Any] = {
        "task_id": task_id,
        "kind": TASK_KIND,
        "environment": record.slug,
        "refresh_kind": record.refresh_kind,
        "status": _TASK_STATUS.get(record.refresh_status, record.refresh_status),
        "started_at": record.refresh_started_at,
        "finished_at": record.refresh_finished_at,
        "step": step.label if step is not None else None,
        "steps": [item.model_dump(mode="json") for item in record.refresh_steps],
        "error": record.refresh_error,
        "snapshot_status": record.snapshot_status,
    }
    if not with_output:
        return state
    # While a script runs its trace only exists on the builder; once the refresh
    # settles the builder is gone and the record holds the whole log.
    if step is not None and step.log_path and record.refresh_sandbox_id:
        state["output"] = await _read_builder_log(record.refresh_sandbox_id, step.log_path)
        state["output_source"] = "builder"
        state["output_path"] = step.log_path
        state["output_truncated"] = True
    else:
        state["output"] = record.refresh_log
        state["output_source"] = "record"
    return state


async def task_list() -> list[dict[str, Any]]:
    """Every environment refresh worth reporting, newest attempt first.

    Skips the live trace read: a list must not open a connection per builder.
    """
    tasks = [
        await task_status(refresh_task_id(record.refresh_run_id), with_output=False)
        for record in await ENVIRONMENTS.list_all()
        if record.refresh_run_id and record.refresh_status != "never"
    ]
    return sorted(tasks, key=lambda task: task.get("started_at") or "", reverse=True)


async def task_stop(task_id: str) -> dict[str, Any]:
    """Cancel a running refresh. The previous snapshot stays in place."""
    record = await _record_for_task(task_id)
    if record is None:
        return {"error": "task not found", "task_id": task_id}
    if record.refresh_status != "refreshing":
        return await task_status(task_id)
    run_id = task_id.removeprefix(f"{TASK_PREFIX}-")
    try:
        await _client().runs.cancel(None, run_id)
    except Exception:
        logger.warning("Could not cancel refresh run %s", run_id, exc_info=True)
        return {"error": "could not cancel the refresh run", "task_id": task_id}
    await ENVIRONMENTS.mark_refresh_settled(record.slug, "failed", error="cancelled")
    if record.refresh_sandbox_id:
        await _release_builder_sandbox(record.refresh_sandbox_id)
    return await task_status(task_id)


async def run_environment_refresh_tick(
    slug: str | None, kind: RefreshKind = "full"
) -> dict[str, Any]:
    """Scheduler entrypoint: refresh one environment, or every scripted one."""
    if slug:
        return await refresh_environment(slug, kind)
    results = [
        await refresh_environment(record.slug, kind)
        for record in await ENVIRONMENTS.list_all()
        if record.setup_script
    ]
    return {"status": "swept", "refreshed": results}
