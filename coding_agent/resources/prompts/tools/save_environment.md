Create an environment, or update an existing one's configuration.

Does not touch the environment's snapshot: capture that separately with
``capture_environment_snapshot`` once this sandbox is provisioned.

Args:
    name: Display name. Also the snapshot name stem, so keep it short and
        hyphenated (``langsmith-monorepo``). Saving under an existing name
        updates that environment rather than creating a second one. The name
        ``default`` is the environment every run boots from; any other name
        is a draft nobody boots from.
    prompt: The complete instruction text appended to every run's system
        prompt in this environment. This is a full replacement — pass the
        whole text, not a delta. Empty string clears it.
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
    ``{"ok": True, "environment": {...}, "created": bool}``.
