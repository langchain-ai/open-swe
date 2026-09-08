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
  ``UPDATE_INTERVAL_SECONDS`` kicks one off in the background. The triggering
  run boots from the snapshot it found; the fresh one lands for the next. So an
  idle environment costs nothing, a busy one is at most an hour stale, and no
  run ever waits on or fails because of the script.

The outcome — status, kind, timestamps, a capped log — lands on the environment
record for the dashboard. A failed refresh of either kind leaves the previous
snapshot in place: runs keep booting from the last image that worked.
"""

import hashlib
import logging
import os
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client

from agent.dashboard.environments import (
    ENVIRONMENTS,
    SETUP_SCRIPT_PATH,
    UPDATE_SCRIPT_PATH,
    Environment,
    RefreshKind,
    capture_environment_snapshot,
    require_capture_support,
    script_command,
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


def _client():
    return get_client()


def _timeout(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


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
                script_command(record.setup_script, SETUP_SCRIPT_PATH),
                _timeout("ENVIRONMENT_REFRESH_TIMEOUT_SECONDS", DEFAULT_SCRIPT_TIMEOUT_SECONDS),
            )
        )
    if record.update_script:
        steps.append(
            (
                "update",
                script_command(record.update_script, UPDATE_SCRIPT_PATH),
                _timeout("ENVIRONMENT_UPDATE_TIMEOUT_SECONDS", DEFAULT_UPDATE_TIMEOUT_SECONDS),
            )
        )
    return steps


def is_update_due(record: Environment) -> bool:
    """Whether a new sandbox for this environment should kick off an update.

    Only when there is something to update (a script and a ready snapshot), no
    refresh is already running, and the last refresh of either kind — success
    or failure — is older than the interval. Counting failures keeps a broken
    network from retrying on every single sandbox creation.
    """
    if not record.update_script or record.ready_snapshot_id is None:
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
        backend = await _create_builder_sandbox(record, base)
        sandbox_id = str(backend.id)
        # Every script runs before the capture, so a snapshot only ships once the
        # whole of what produced it worked.
        for label, command, timeout in _scripts_to_run(record, kind):
            result = await backend.aexecute(command, timeout=timeout)
            log = f"{log}\n--- {label} script ---\n{result.output or ''}".strip()
            if result.exit_code != 0:
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
        await capture_environment_snapshot(
            slug,
            sandbox_id,
            timeout=_timeout(
                "ENVIRONMENT_CAPTURE_TIMEOUT_SECONDS", DEFAULT_CAPTURE_TIMEOUT_SECONDS
            ),
        )
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

    For callers that must not block for minutes — an HTTP request, or a sandbox
    being created. An admin thread awaits ``refresh_environment`` directly
    instead, so the model sees the log and can fix the script.
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
    return run_id if isinstance(run_id, str) else None


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
