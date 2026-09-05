"""Admin-thread tools for managing environments.

Wired into the agent only for admin threads (see ``agent/server.py``). Every tool
re-checks the triggering user against ``CONFIGURED_ADMINS`` so a thread whose
metadata says "admin" cannot act on behalf of someone who is not one.
"""

import logging
from typing import Any

from agent.dashboard import environment_refresh as refresh
from agent.dashboard import environments as store
from agent.tools.admin_gate import configurable as _configurable
from agent.tools.admin_gate import require_admin

logger = logging.getLogger(__name__)


def _require_admin() -> str | None:
    return require_admin("manage environments")


# Deliberately narrower than the record: the agent has no use for authorship
# or the source sandbox.
_SUMMARY_FIELDS = {
    "name",
    "slug",
    "prompt",
    "repos",
    "mem_bytes",
    "vcpus",
    "fs_capacity_bytes",
    "create_params",
    "setup_script",
    "init_script",
    "base_snapshot_id",
    "snapshot_status",
    "snapshot_id",
    "snapshot_name",
    "snapshot_tag",
    "status_message",
    "last_captured_at",
    "refresh_status",
    "refresh_finished_at",
    "refresh_error",
}


def _summary(record: store.Environment) -> dict[str, Any]:
    summary = record.model_dump(mode="json", include=_SUMMARY_FIELDS)
    summary["refresh_log_excerpt"] = store.log_excerpt(record.refresh_log)
    return summary


def _refresh_result(outcome: dict[str, Any]) -> dict[str, Any]:
    """Hand the model the outcome of a refresh, with enough log to act on."""
    log = store.log_excerpt(outcome.get("log"), lines=30)
    if outcome.get("status") == "success":
        return {"ok": True, "refreshed": True, "seconds": outcome.get("seconds"), "log": log}
    return {
        "ok": False,
        "refreshed": False,
        "error": outcome.get("error") or f"refresh {outcome.get('status')}",
        "failed_script": outcome.get("script"),
        "log": log,
    }


def _scripts_changed(existing: store.Environment | None, record: store.Environment) -> bool:
    """Whether this save changed what a rebuild would produce.

    A prompt-only edit must not pay for a rebuild; a script edit must not ship
    unverified.
    """
    if existing is None:
        return True
    return (
        existing.setup_script != record.setup_script
        or existing.init_script != record.init_script
        or existing.base_snapshot_id != record.base_snapshot_id
    )


async def list_environments() -> dict[str, Any]:
    """List every environment with its snapshot state.

    The one named ``default`` is what runs boot from; the rest are drafts.

    Returns:
        ``{"ok": True, "environments": [...]}``.
    """
    if error := _require_admin():
        return {"ok": False, "error": error}
    records = await store.ENVIRONMENTS.list_all()
    return {
        "ok": True,
        "environments": [
            {**_summary(record), "is_default": record.slug == store.DEFAULT_ENVIRONMENT_SLUG}
            for record in records
        ],
    }


async def save_environment(
    name: str,
    prompt: str,
    setup_script: str | None = None,
    init_script: str | None = None,
    base_snapshot_id: str | None = None,
    clear_base_snapshot_id: bool = False,
    snapshot_name: str | None = None,
    repos: list[str] | None = None,
    mem_bytes: int | None = None,
    vcpus: int | None = None,
    fs_capacity_bytes: int | None = None,
    clear_sizing: bool = False,
    create_params: dict[str, Any] | None = None,
    clear_create_params: bool = False,
) -> dict[str, Any]:
    """Create an environment, or update an existing one's definition.

    Saving a new or changed script runs it immediately and waits: the setup
    script and then the init script execute on a throwaway sandbox booted from
    the base snapshot, and the snapshot is captured only if both succeed. The
    result comes back under ``refresh`` with the log, so a broken script can be
    fixed and saved again. A save that leaves the scripts alone rebuilds nothing.

    Args:
        name: Display name. Also the snapshot name stem, so keep it short and
            hyphenated (``langsmith-monorepo``). Saving under an existing name
            updates that environment rather than creating a second one. The name
            ``default`` is the environment every run boots from; any other name
            is a draft nobody boots from.
        prompt: The complete instruction text appended to every run's system
            prompt in this environment. This is a full replacement — pass the
            whole text, not a delta. Empty string clears it.
        setup_script: Bash script that provisions a sandbox from the base
            snapshot: clone the repos, install `rg`, `gh`, the toolchains and
            dependencies, warm caches. It runs unattended in a throwaway sandbox
            every night, so it must be non-interactive and safe to re-run from
            scratch, and it must never write a secret or a proxy credential to
            disk. A full replacement, not a delta; empty string clears it.
        init_script: Optional bash script every new sandbox for this environment
            runs once after booting from the snapshot, for what goes stale in an
            image (``git pull``, a dependency sync). It is on the critical path
            before the first model call, so keep it short. A full replacement;
            empty string clears it.
        base_snapshot_id: Optional snapshot the setup script provisions from,
            when this environment needs something other than the configured base.
        clear_base_snapshot_id: Go back to the configured base snapshot. Cannot be
            combined with ``base_snapshot_id``.
        snapshot_name: Optional name this environment publishes its snapshot
            under, defaulting to ``<prefix>-environment-<slug>``. It must not
            contain a colon — that separates the name from the tag — and it is
            stable: every refresh re-publishes ``name:latest`` under it.
        repos: Optional ``owner/repo`` list this environment covers, for the
            dashboard. Does not clone anything by itself.
        mem_bytes: Optional memory capacity for newly-created sandbox VMs.
        vcpus: Optional virtual CPU count for newly-created sandbox VMs.
        fs_capacity_bytes: Optional filesystem capacity for newly-created sandbox VMs.
            Omitted sizing fields keep provider defaults when creating an environment,
            or preserve the existing values when updating one.
        clear_sizing: Restore provider defaults by clearing all three sizing overrides.
            Cannot be combined with a sizing value.
        create_params: Additional LangSmith sandbox create-body fields, such as
            ``_internal_runtime`` or ``proxy_config``. This object is persisted and
            must never contain secrets or authentication credentials. Omit it when
            updating to preserve the existing object.
        clear_create_params: Clear all additional create parameters. Cannot be combined
            with ``create_params``.

    Returns:
        ``{"ok": True, "environment": {...}, "created": bool}``, plus ``refresh``
        when a changed script was run.
    """
    if error := _require_admin():
        return {"ok": False, "error": error}
    sizing = {
        "mem_bytes": mem_bytes,
        "vcpus": vcpus,
        "fs_capacity_bytes": fs_capacity_bytes,
    }
    if clear_sizing and any(value is not None for value in sizing.values()):
        return {"ok": False, "error": "clear_sizing cannot be combined with sizing values"}
    if clear_create_params and create_params is not None:
        return {"ok": False, "error": "clear_create_params cannot be combined with create_params"}
    if clear_base_snapshot_id and base_snapshot_id is not None:
        return {
            "ok": False,
            "error": "clear_base_snapshot_id cannot be combined with base_snapshot_id",
        }
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    existing = await store.ENVIRONMENTS.get(slug)
    try:
        if existing is None:
            login = _configurable().github_login
            record = await store.ENVIRONMENTS.create(
                store.EnvironmentCreate(
                    name=name,
                    prompt=prompt,
                    setup_script=setup_script or "",
                    init_script=init_script or "",
                    base_snapshot_id=base_snapshot_id,
                    snapshot_name=snapshot_name,
                    repos=repos or [],
                    mem_bytes=mem_bytes,
                    vcpus=vcpus,
                    fs_capacity_bytes=fs_capacity_bytes,
                    create_params=create_params or {},
                ),
                login if isinstance(login, str) else "open-swe",
            )
        else:
            update_values: dict[str, Any] = {"name": name, "prompt": prompt, "repos": repos}
            update_values.update(
                dict.fromkeys(sizing)
                if clear_sizing
                else {field: value for field, value in sizing.items() if value is not None}
            )
            if create_params is not None:
                update_values["create_params"] = create_params
            elif clear_create_params:
                update_values["create_params"] = {}
            if setup_script is not None:
                update_values["setup_script"] = setup_script
            if init_script is not None:
                update_values["init_script"] = init_script
            if snapshot_name is not None:
                update_values["snapshot_name"] = snapshot_name
            if base_snapshot_id is not None:
                update_values["base_snapshot_id"] = base_snapshot_id
            elif clear_base_snapshot_id:
                update_values["base_snapshot_id"] = None
            record = await store.ENVIRONMENTS.apply_update(
                slug,
                store.EnvironmentUpdate(**update_values),
            )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Failed to save environment %s", slug)
        return {"ok": False, "error": f"failed to save environment: {exc}"}

    if not record.setup_script:
        return {"ok": True, "environment": _summary(record), "created": existing is None}

    await refresh.ensure_refresh_cron(slug)
    result: dict[str, Any] = {
        "ok": True,
        "environment": _summary(record),
        "created": existing is None,
    }
    if _scripts_changed(existing, record):
        outcome = _refresh_result(await refresh.refresh_environment(slug))
        result["refresh"] = outcome
        result["ok"] = outcome["ok"]
        if outcome["ok"]:
            result["environment"] = _summary(await store.ENVIRONMENTS.get(slug) or record)
    return result


async def refresh_environment(name: str) -> dict[str, Any]:
    """Rebuild an environment's snapshot by running its scripts, and wait for it.

    A throwaway sandbox boots from the base snapshot, runs the setup script and
    then the init script, and the result is captured as this environment's
    snapshot only if both succeed. Blocks until it finishes — minutes for a real
    setup script — and returns the log so a failure can be fixed and re-run.

    A failed refresh keeps the previous snapshot, so runs never drop to the base
    image because a script broke. This also runs nightly on its own.

    Args:
        name: Name of an environment whose ``setup_script`` is already saved.

    Returns:
        ``{"ok": True, "refreshed": True, "log": ...}``, or ``ok: False`` with the
        failing script and its log.
    """
    if error := _require_admin():
        return {"ok": False, "error": error}
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    record = await store.ENVIRONMENTS.get(slug)
    if record is None:
        return {"ok": False, "error": f"no environment named {name!r}; call save_environment first"}
    if not record.setup_script:
        return {"ok": False, "error": f"environment {name!r} has no setup_script to run"}
    if refresh.is_refresh_in_flight(record):
        return {"ok": False, "error": f"a refresh of {name!r} is already running"}

    return _refresh_result(await refresh.refresh_environment(slug))


async def delete_environment(name: str) -> dict[str, Any]:
    """Delete an environment, its snapshot, and its nightly refresh.

    Deleting ``default`` sends runs back to the per-repo and base snapshots.
    Confirm with the user first: the snapshot cannot be recovered, only rebuilt.

    Args:
        name: Environment to delete.

    Returns:
        ``{"ok": True, "deleted": bool}``.
    """
    if error := _require_admin():
        return {"ok": False, "error": error}
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        deleted = await store.ENVIRONMENTS.remove(slug)
    except Exception as exc:
        logger.exception("Failed to delete environment %s", slug)
        return {"ok": False, "error": f"failed to delete environment: {exc}"}
    if not deleted:
        return {"ok": False, "error": f"no environment named {name!r}"}
    return {"ok": True, "deleted": True}
