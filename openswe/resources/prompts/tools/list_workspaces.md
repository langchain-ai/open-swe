List retained legacy workspace records and their snapshot state on an authorized private admin surface.
Cloud Environments pages and pickers are removed; ordinary routing bindings live in **Integrations** (`/admin/integrations`).

While **Use Robin-wide Defaults** is off, ``default`` supplies legacy fallback execution and other records retain their routing and overrides.
When enabled, every environment uses the same Robin-wide execution and instance defaults; this list is compatibility/rollback data, not a list of selectable execution environments.
Use `read_execution_defaults` and `read_execution_image` for the global configuration and image.

Returns: ``{"ok": True, "workspaces": [...]}``.
