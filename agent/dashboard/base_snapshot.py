"""Scheduled warming of the sandbox repo cache.

On the configured schedule the repos Open SWE actually works on (from
:mod:`agent.dashboard.repo_clone_stats`) are cloned into a hidden cache dir as
ready-to-use checkouts, so a run takes one with a rename instead of paying for
a clone.

Two modes, picked from ``SANDBOX_TYPE``:

- ``snapshot`` (LangSmith) -- warm a throwaway builder, then capture it as an
  image. New sandboxes boot from that image with the cache already present.
  Incremental: the builder boots from the previous capture, so each warm only
  fetches what changed and anything baked into a checkout survives. It falls
  back to the pristine seed when the seed itself changes, or when an operator
  asks for a clean rebuild.
- ``cache`` (providers whose filesystem persists, e.g. ``local``) -- warm the
  shared work dir in place. No image is involved; the cache simply stays.

Providers that are neither fail loudly rather than warming a throwaway sandbox
that is discarded moments later.

Everything operators tune lives in one store record edited from the admin
Snapshots page -- there is no env-var configuration. Resolution stays additive:
runs fall back to the seed snapshot whenever no ready capture exists, so a
failed rebuild degrades to today's behavior.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client
from pydantic import BaseModel, Field, field_validator

from ..utils.repo_clone import (
    PREFERRED_WORK_DIR,
    build_mirror_sweep_script,
    parse_mirror_sweep_output,
)
from .repo_clone_stats import repos_to_preclone
from .schedules import normalize_cron_schedule

logger = logging.getLogger(__name__)

BASE_SNAPSHOT_NAMESPACE: list[str] = ["base_snapshot"]
BASE_SNAPSHOT_KEY = "current"

SNAPSHOT_NAME_PREFIX = "openswe-base-"

CLONE_TIMEOUT_SECONDS = 900
CAPTURE_TIMEOUT_SECONDS = 900

# The builder is reclaimed by the platform, never by an explicit delete (see
# tests/sandbox/test_langsmith_sandbox_config.py). Idle only starts counting
# once the clone sweep and capture stop touching the box, so a short TTL is
# safe and keeps a crashed build from lingering.
BUILDER_IDLE_TTL_SECONDS = 600
BUILDER_DELETE_AFTER_STOP_SECONDS = 60

STATUS_MESSAGE_MAX_CHARS = 1000
SCRIPT_MAX_CHARS = 20_000
HOOK_TIMEOUT_SECONDS = 1800

# Past this, a record still on ``building`` is treated as a crashed run rather
# than a slow one. Comfortably above the clone + capture timeouts.
STALE_BUILD_SECONDS = 2 * (CLONE_TIMEOUT_SECONDS + CAPTURE_TIMEOUT_SECONDS)

PROGRESS_POLL_SECONDS = 2

DEFAULT_SCHEDULE = "0 9 * * *"  # 09:00 UTC, after the analyzer's 05:00-08:59 window
DEFAULT_PRECLONE_LIMIT = 10
DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_KEEP_SNAPSHOTS = 3

MIN_PRECLONE_LIMIT, MAX_PRECLONE_LIMIT = 1, 50
MIN_MAX_AGE_DAYS, MAX_MAX_AGE_DAYS = 1, 365
MIN_KEEP_SNAPSHOTS, MAX_KEEP_SNAPSHOTS = 1, 10

_SCHEDULER_ASSISTANT_ID = "scheduler"
_REBUILD_TASK = "rebuild_base_snapshot"


class BaseSnapshotSettings(BaseModel):
    """Operator-tunable knobs for the nightly rebuild."""

    enabled: bool = False
    schedule: str = DEFAULT_SCHEDULE
    preclone_limit: int = Field(default=DEFAULT_PRECLONE_LIMIT)
    max_age_days: int = Field(default=DEFAULT_MAX_AGE_DAYS)
    keep_snapshots: int = Field(default=DEFAULT_KEEP_SNAPSHOTS)
    # Run in the builder around the repo sweep. This is where work that should
    # be baked into the image goes -- installing dependencies, warming package
    # caches -- so a run inherits it instead of doing it itself.
    pre_script: str = ""
    post_script: str = ""

    @field_validator("pre_script", "post_script")
    @classmethod
    def _script_len(cls, v: str) -> str:
        if len(v) > SCRIPT_MAX_CHARS:
            raise ValueError(f"script must be at most {SCRIPT_MAX_CHARS} characters")
        return v

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, v: str) -> str:
        return normalize_cron_schedule(v)

    @field_validator("preclone_limit")
    @classmethod
    def _preclone_limit(cls, v: int) -> int:
        if not MIN_PRECLONE_LIMIT <= v <= MAX_PRECLONE_LIMIT:
            raise ValueError(
                f"preclone_limit must be between {MIN_PRECLONE_LIMIT} and {MAX_PRECLONE_LIMIT}"
            )
        return v

    @field_validator("max_age_days")
    @classmethod
    def _max_age_days(cls, v: int) -> int:
        if not MIN_MAX_AGE_DAYS <= v <= MAX_MAX_AGE_DAYS:
            raise ValueError(
                f"max_age_days must be between {MIN_MAX_AGE_DAYS} and {MAX_MAX_AGE_DAYS}"
            )
        return v

    @field_validator("keep_snapshots")
    @classmethod
    def _keep_snapshots(cls, v: int) -> int:
        if not MIN_KEEP_SNAPSHOTS <= v <= MAX_KEEP_SNAPSHOTS:
            raise ValueError(
                f"keep_snapshots must be between {MIN_KEEP_SNAPSHOTS} and {MAX_KEEP_SNAPSHOTS}"
            )
        return v


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _coerce_settings(raw: object) -> BaseSnapshotSettings:
    """Read stored settings, falling back to defaults for anything invalid."""
    if not isinstance(raw, dict):
        return BaseSnapshotSettings()
    try:
        return BaseSnapshotSettings.model_validate(raw)
    except Exception:  # noqa: BLE001
        logger.warning("Stored base snapshot settings are invalid; using defaults")
        return BaseSnapshotSettings()


async def _read_record() -> dict[str, Any]:
    try:
        item = await _client().store.get_item(BASE_SNAPSHOT_NAMESPACE, BASE_SNAPSHOT_KEY)
    except Exception:  # noqa: BLE001
        logger.debug("base snapshot lookup failed", exc_info=True)
        return {}
    if item is None:
        return {}
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else {}


async def get_base_snapshot_record() -> dict[str, Any]:
    """Return the stored record with settings always populated."""
    record = await _read_record()
    return {**record, "settings": _coerce_settings(record.get("settings")).model_dump()}


async def get_base_snapshot_settings() -> BaseSnapshotSettings:
    return _coerce_settings((await _read_record()).get("settings"))


async def _put_record(value: dict[str, Any]) -> None:
    await _client().store.put_item(BASE_SNAPSHOT_NAMESPACE, BASE_SNAPSHOT_KEY, value)


async def resolve_base_snapshot_id() -> str | None:
    """Return the ready nightly snapshot id, or ``None`` to use the seed.

    Never raises: any failure resolves to ``None`` so sandbox creation falls
    back to ``DEFAULT_SANDBOX_SNAPSHOT_ID``.
    """
    try:
        record = await _read_record()
    except Exception:  # noqa: BLE001
        logger.debug("base snapshot resolution failed", exc_info=True)
        return None
    if not record or not _coerce_settings(record.get("settings")).enabled:
        return None
    # Deliberately not gated on status: a snapshot_id is only written after a
    # capture succeeds, so the last good one stays usable when a later rebuild
    # fails. Gating on ``ready`` would drop the whole warm cache on one
    # transient hook, clone, or capture failure.
    snapshot_id = record.get("snapshot_id")
    return snapshot_id if isinstance(snapshot_id, str) and snapshot_id else None


async def update_base_snapshot_settings(settings: BaseSnapshotSettings) -> dict[str, Any]:
    """Persist settings and bring the rebuild cron in line with them."""
    record = await _read_record()
    cron_id = await _sync_cron(record, settings)
    record = {
        **record,
        "settings": settings.model_dump(),
        "cron_id": cron_id,
        "cron_schedule": settings.schedule if cron_id else None,
        "updated_at": _now_iso(),
    }
    await _put_record(record)
    return record


async def mark_base_snapshot_building() -> dict[str, Any]:
    """Flip the record to ``building`` before the rebuild starts.

    Done up front, and awaited by the route before it returns, so the UI starts
    polling immediately instead of watching a stale ``ready`` until the whole
    rebuild finishes.
    """
    record = await _read_record()
    record = {
        **record,
        "status": "building",
        "status_message": None,
        "build_started_at": _now_iso(),
        "progress": {"phase": "starting", "completed": 0, "total": 0},
        "updated_at": _now_iso(),
    }
    await _put_record(record)
    return record


def is_base_snapshot_build_stale(record: dict[str, Any]) -> bool:
    """Whether a ``building`` record is old enough to be a crashed run.

    Without this a process that dies mid-rebuild wedges the record on
    ``building`` forever, and the UI never offers the button again.
    """
    if record.get("status") != "building":
        return False
    started = record.get("build_started_at")
    if not isinstance(started, str) or not started.strip():
        return True
    try:
        parsed = datetime.fromisoformat(started)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - parsed).total_seconds() > STALE_BUILD_SECONDS


async def _set_progress(phase: str, completed: int, total: int) -> None:
    """Best-effort progress write; a lost update must not fail the build."""
    try:
        record = await _read_record()
        record["progress"] = {"phase": phase, "completed": completed, "total": total}
        record["updated_at"] = _now_iso()
        await _put_record(record)
    except Exception:  # noqa: BLE001
        logger.debug("Could not write base snapshot progress", exc_info=True)


def _seed_snapshot_id() -> str | None:
    """Snapshot the builder boots from, or ``None`` to take the platform default.

    Always the configured seed, never the previous capture, so a bad clone can't
    compound across rebuilds.
    """
    return (os.environ.get("DEFAULT_SANDBOX_SNAPSHOT_ID") or "").strip() or None


async def _resolve_builder_work_dir(sandbox: Any) -> str:
    """The work dir to warm, which must be the one runs will read back.

    Deliberately not the shell's cwd: a sandbox booted from a capture starts at
    ``/`` because WORKDIR is image metadata that capture does not carry, so
    trusting ``pwd`` would warm a different directory each time depending on
    how the builder happened to boot. Matches the preference
    ``aresolve_sandbox_work_dir`` applies at run time.
    """
    try:
        result = await sandbox.run(
            f"mkdir -p {shlex.quote(PREFERRED_WORK_DIR)} "
            f"&& cd {shlex.quote(PREFERRED_WORK_DIR)} && pwd"
        )
        resolved = (result.stdout or "").strip().splitlines()
        if resolved and resolved[-1].startswith("/"):
            return resolved[-1]
    except Exception:  # noqa: BLE001
        logger.debug("Could not prepare the builder work dir", exc_info=True)
    return PREFERRED_WORK_DIR


async def _run_clone_sweep(sandbox: Any, work_dir: str, repos: list[str]) -> Any:
    """Run the clone sweep, publishing progress as each repo lands.

    The sweep prints one line per repo, so a reader task can count them while
    the (single, long) command is still running. ``on_stdout`` is synchronous,
    hence the buffer-and-poll split rather than awaiting inside the callback.
    """
    chunks: list[str] = []
    total = len(repos)

    async def _publish() -> None:
        last = -1
        while True:
            await asyncio.sleep(PROGRESS_POLL_SECONDS)
            done, failed = parse_mirror_sweep_output("".join(chunks))
            completed = len(done) + len(failed)
            if completed != last:
                await _set_progress("cloning", completed, total)
                last = completed

    await _set_progress("cloning", 0, total)
    publisher = asyncio.create_task(_publish())
    try:
        return await sandbox.run(
            build_mirror_sweep_script(work_dir, repos, proxy_auth=True),
            timeout=CLONE_TIMEOUT_SECONDS,
            on_stdout=chunks.append,
        )
    finally:
        publisher.cancel()


def _sandbox_provider() -> str:
    return os.getenv("SANDBOX_TYPE", "langsmith")


# Providers whose sandbox filesystem outlives a single run. There, warming the
# cache in place is durable on its own and no image capture is involved.
PERSISTENT_PROVIDERS = frozenset({"local"})


async def _warm_shared_cache(
    repos: list[str], settings: BaseSnapshotSettings
) -> tuple[list[str], list[str]]:
    """Fill the cache through the generic backend, for providers without capture.

    Uses ``create_sandbox`` rather than a provider SDK, so this works anywhere
    ``SANDBOX_FACTORIES`` does. The backend protocol has no output streaming, so
    progress here is start-and-finish rather than per-repo.
    """
    from ..utils.sandbox import create_sandbox
    from ..utils.sandbox_paths import aresolve_sandbox_work_dir

    backend = await create_sandbox()
    work_dir = await aresolve_sandbox_work_dir(backend)

    async def _exec(command: str, timeout: int) -> Any:
        return await backend.aexecute(command, timeout=timeout)

    hook_error = await _run_hook(_exec, "pre_script", settings.pre_script, work_dir)
    if hook_error:
        raise RuntimeError(hook_error)

    await _set_progress("cloning", 0, len(repos))
    result = await backend.aexecute(
        build_mirror_sweep_script(work_dir, repos, proxy_auth=False),
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    output = getattr(result, "output", None) or getattr(result, "stdout", "") or ""
    cloned, failed = parse_mirror_sweep_output(output)
    await _set_progress("cloning", len(cloned) + len(failed), len(repos))

    hook_error = await _run_hook(_exec, "post_script", settings.post_script, work_dir)
    if hook_error:
        raise RuntimeError(hook_error)
    return cloned, failed


async def _run_hook(
    execute: Any,
    label: str,
    script: str,
    work_dir: str,
) -> str | None:
    """Run a pre/post hook in the builder. Returns an error message, or None.

    A failing hook fails the whole build on purpose: a snapshot whose
    dependency install half-ran is worse than no new snapshot, because every
    run would inherit the broken state and none would report why.
    """
    if not script.strip():
        return None
    await _set_progress(label, 0, 0)
    logger.info("Running %s script in the base snapshot builder", label)
    wrapped = f"set -e\ncd {shlex.quote(work_dir)}\n{script}"
    result = await execute(wrapped, HOOK_TIMEOUT_SECONDS)
    output = getattr(result, "stdout", None) or getattr(result, "output", "") or ""
    exit_code = getattr(result, "exit_code", None)
    if exit_code not in (0, None):
        return f"{label} script exited {exit_code}: {output[-500:]}"
    return None


async def _prune_old_snapshots(client: Any, keep_snapshot_id: str, keep: int) -> None:
    """Delete superseded nightly snapshots, keeping the newest few."""
    try:
        snapshots = await client.list_snapshots(name_contains=SNAPSHOT_NAME_PREFIX, limit=100)
    except Exception:  # noqa: BLE001
        logger.debug("Could not list base snapshots for pruning", exc_info=True)
        return

    ours = [s for s in snapshots if (s.name or "").startswith(SNAPSHOT_NAME_PREFIX)]
    ours.sort(key=lambda s: s.name or "", reverse=True)
    for snapshot in ours[keep:]:
        if snapshot.id == keep_snapshot_id:
            continue
        try:
            await client.delete_snapshot(snapshot.id)
            logger.info("Pruned old base snapshot %s (%s)", snapshot.id, snapshot.name)
        except Exception:  # noqa: BLE001
            logger.debug("Could not prune base snapshot %s", snapshot.id, exc_info=True)


def _builder_base_snapshot(
    existing: dict[str, Any], seed: str | None, from_scratch: bool
) -> str | None:
    """Snapshot the builder boots from: yesterday's capture, or the seed.

    Building on the previous capture keeps the warm incremental -- only the
    commits since last time, and baked dependencies survive instead of being
    reinstalled nightly.

    It falls back to the seed when there is nothing to build on, when the
    operator asks for a clean rebuild, or when the seed itself has changed --
    that last one matters because a new base image (new tools, new runtimes)
    would otherwise never reach the chain.
    """
    if from_scratch:
        return seed
    if existing.get("status") != "ready" or existing.get("mode") != "snapshot":
        return seed
    if existing.get("seed_snapshot_id") != seed:
        logger.info("Seed snapshot changed; rebuilding the base snapshot from scratch")
        return seed
    previous = existing.get("snapshot_id")
    return previous if isinstance(previous, str) and previous else seed


async def rebuild_base_snapshot(from_scratch: bool = False) -> dict[str, Any]:
    """Build the next base snapshot and persist the result.

    Incremental by default: the builder boots from the previous capture and the
    warm only fetches what changed. ``from_scratch`` discards that lineage and
    starts from the seed, which is the escape hatch when accumulated state has
    gone wrong.

    Returns the stored record. On failure the previous ready snapshot id is
    kept so runs keep booting from the last good capture.
    """
    from ..utils.github_app import get_github_app_installation_token

    existing = await _read_record()
    settings = _coerce_settings(existing.get("settings"))
    # Never derived from the record's current status: the route marks it
    # ``building`` before this runs, so echoing that back on a terminal path
    # would leave the UI polling a build that already finished.
    idle_status = "ready" if existing.get("snapshot_id") else "none"
    previous = await mark_base_snapshot_building()
    started_at = _now_iso()

    async def _store(**fields: Any) -> dict[str, Any]:
        # Every terminal write clears the in-flight markers, so a finished run
        # can never leave the UI stuck on a progress bar.
        record = {
            **previous,
            **fields,
            "progress": None,
            "build_started_at": None,
            "last_attempt_at": started_at,
            "updated_at": _now_iso(),
        }
        await _put_record(record)
        return record

    async def _fail(message: str) -> dict[str, Any]:
        return await _store(status="failed", status_message=message[:STATUS_MESSAGE_MAX_CHARS])

    seed_snapshot_id = _seed_snapshot_id()
    provider = _sandbox_provider()

    repos = await repos_to_preclone(
        limit=settings.preclone_limit, max_age_days=settings.max_age_days
    )
    if not repos:
        logger.info("No repos in the clone ledger yet; skipping base snapshot rebuild")
        return await _store(status=idle_status, status_message="no repos recorded yet")

    if provider != "langsmith":
        # No capture API outside LangSmith. Where the sandbox filesystem
        # persists, warming it in place is enough; where it doesn't, warming a
        # throwaway box would be discarded, so say so rather than appear to work.
        if provider not in PERSISTENT_PROVIDERS:
            return await _fail(
                f"SANDBOX_TYPE={provider} has no snapshot capture and no persistent "
                "filesystem, so a warmed cache would not survive the run that built it"
            )
        try:
            cloned, failed = await _warm_shared_cache(repos, settings)
        except Exception as e:  # noqa: BLE001
            logger.warning("Cache warm failed", exc_info=True)
            return await _fail(f"{type(e).__name__}: {e}")
        if not cloned:
            return await _fail(f"no repos cloned successfully (failed: {', '.join(failed)})")
        logger.info("Warmed repo cache with %d repos (%d failed)", len(cloned), len(failed))
        return await _store(
            status="ready",
            status_message=None,
            mode="cache",
            snapshot_id=None,
            snapshot_name=None,
            repos=cloned,
            failed_repos=failed,
            built_at=_now_iso(),
        )

    from ..integrations.langsmith import _configure_github_proxy, get_async_sandbox_client

    builder_base = _builder_base_snapshot(existing, seed_snapshot_id, from_scratch)
    logger.info(
        "Warming base snapshot from %s",
        "seed" if builder_base == seed_snapshot_id else f"previous capture {builder_base}",
    )

    token = await get_github_app_installation_token()
    if not token:
        return await _fail("GitHub App installation token unavailable")

    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    snapshot_name = f"{SNAPSHOT_NAME_PREFIX}{timestamp}"
    builder_name = f"openswe-base-builder-{timestamp}"

    client = get_async_sandbox_client()
    sandbox = None
    try:
        sandbox = await client.create_sandbox(
            snapshot_id=builder_base,
            name=builder_name,
            idle_ttl_seconds=BUILDER_IDLE_TTL_SECONDS,
            delete_after_stop_seconds=BUILDER_DELETE_AFTER_STOP_SECONDS,
        )
        await _configure_github_proxy(sandbox.name, token)

        work_dir = await _resolve_builder_work_dir(sandbox)

        async def _exec(command: str, timeout: int) -> Any:
            return await sandbox.run(command, timeout=timeout)

        hook_error = await _run_hook(_exec, "pre_script", settings.pre_script, work_dir)
        if hook_error:
            return await _fail(hook_error)

        result = await _run_clone_sweep(sandbox, work_dir, repos)
        cloned, failed = parse_mirror_sweep_output(result.stdout or "")
        if not cloned:
            return await _fail(f"no repos cloned successfully (failed: {', '.join(failed)})")

        hook_error = await _run_hook(_exec, "post_script", settings.post_script, work_dir)
        if hook_error:
            return await _fail(hook_error)

        # The capture is one blocking call with no interim signal, so this phase
        # is honestly indeterminate rather than a fake-advancing bar.
        await _set_progress("capturing", len(cloned), len(repos))
        snapshot = await client.capture_snapshot(
            sandbox.name, snapshot_name, timeout=CAPTURE_TIMEOUT_SECONDS
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Base snapshot rebuild failed", exc_info=True)
        return await _fail(f"{type(e).__name__}: {e}")
    finally:
        if sandbox is not None:
            try:
                await client.stop_sandbox(sandbox.name)
            except Exception:  # noqa: BLE001
                logger.debug("Could not stop base snapshot builder %s", builder_name, exc_info=True)

    record = await _store(
        snapshot_id=snapshot.id,
        snapshot_name=snapshot_name,
        seed_snapshot_id=seed_snapshot_id,
        mode="snapshot",
        status="ready",
        status_message=None,
        repos=cloned,
        failed_repos=failed,
        built_at=_now_iso(),
    )
    logger.info(
        "Built base snapshot %s with %d pre-cloned repos (%d failed)",
        snapshot.id,
        len(cloned),
        len(failed),
    )

    await _prune_old_snapshots(client, snapshot.id, settings.keep_snapshots)
    return record


async def _delete_cron(cron_id: str) -> None:
    try:
        await _client().crons.delete(cron_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not delete base snapshot cron %s", cron_id, exc_info=True)


async def _sync_cron(record: dict[str, Any], settings: BaseSnapshotSettings) -> str | None:
    """Make the registered cron match ``settings``. Returns the live cron id.

    Schedule changes go through delete-then-create so a stale cron can never
    outlive the schedule it was created from.
    """
    existing = record.get("cron_id")
    existing = existing if isinstance(existing, str) and existing else None

    if not settings.enabled:
        if existing:
            await _delete_cron(existing)
        return None

    if existing and record.get("cron_schedule") == settings.schedule:
        return existing
    if existing:
        await _delete_cron(existing)

    try:
        cron = await _client().crons.create(
            _SCHEDULER_ASSISTANT_ID,
            schedule=settings.schedule,
            input={"task": _REBUILD_TASK},
            config={"configurable": {"task": _REBUILD_TASK}},
            metadata={"kind": "base_snapshot_rebuild"},
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to create base snapshot rebuild cron", exc_info=True)
        return None

    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    return cron_id if isinstance(cron_id, str) and cron_id else None


async def sync_base_snapshot_cron() -> str | None:
    """Reconcile the cron with stored settings (idempotent)."""
    record = await _read_record()
    settings = _coerce_settings(record.get("settings"))
    cron_id = await _sync_cron(record, settings)
    if cron_id != record.get("cron_id") or record.get("cron_schedule") != settings.schedule:
        await _put_record(
            {
                **record,
                "cron_id": cron_id,
                "cron_schedule": settings.schedule if cron_id else None,
                "updated_at": _now_iso(),
            }
        )
    return cron_id


def schedule_base_snapshot_cron_sync() -> None:
    """Fire-and-forget cron reconciliation for the FastAPI lifespan hook."""

    async def _run() -> None:
        try:
            await sync_base_snapshot_cron()
        except Exception:  # noqa: BLE001
            logger.debug("Base snapshot cron sync failed", exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        logger.debug("No running loop; skipping base snapshot cron sync")
