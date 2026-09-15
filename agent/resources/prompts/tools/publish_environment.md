Capture this thread's sandbox as the environment's image and record its definition.

Provision the sandbox first with ordinary tools — clone the repos, install
toolchains, warm caches — and call this when it works. Everything on its
filesystem is captured as ``name:latest``; only once that capture succeeds is
the environment created or updated to point at it, so a failed capture leaves
nothing half-written and a failed provision is fixed here, interactively,
rather than in a script run somewhere you cannot see.

Boot from the right image before provisioning: ``sandbox_reset`` with the base
``snapshot_id`` for a rebuild from scratch, or with the environment's current
snapshot to layer one change onto a working image.

``setup_script`` is optional and is the reproducibility contract: when given,
a nightly rebuild runs it from the base snapshot on a throwaway sandbox and
replaces the image only if it succeeds, so drift between what you did by hand
and what the script does shows up as a failed refresh on the Environments page
rather than silently. Leave no secrets or tokens on disk.

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
    update_script: Optional bash script for what goes stale in an image — a
        ``git pull``, a dependency sync. Keep it to seconds: a run whose
        sandbox boots from a snapshot older than an hour runs this script in
        that sandbox before the first model call, and the same creation
        refreshes the snapshot on a throwaway builder in the background so
        later runs skip it. It also runs at the end of every full rebuild,
        which is where a broken one is caught. Output is traced to
        ``/open-swe/environment/logs/update.log``. A full replacement; empty string
        clears it.
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
    ``{"ok": True, "environment": {...}, "created": bool}`` with the new
    snapshot id on the record, or ``ok: False`` with the capture or
    validation error and nothing written.
