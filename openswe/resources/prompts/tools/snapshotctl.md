Inspect and repair a workspace's sandbox image (its snapshot) without a live sandbox: read its state, rebuild it, stop a rebuild, edit the scripts and create params it is built from, and see who changed it.
Workspace admins only.
`status` and `history` require a private admin surface (a private dashboard thread, an authenticated Slack DM, or MCP); the writes also work in an admin thread or a verified sole-writer shared thread, returning only an acknowledgement there.
Every write is recorded in the audit log under the acting admin.

- `action`:
  - `status`: the snapshot runs boot from (`snapshot_id`, `snapshot_status`, `last_captured_at`), the latest refresh (`refresh_status`, `refresh_kind`, `refresh_steps` with each stage's exit code, `refresh_error`, `refresh_task_id`), the end of its log in `refresh_log_tail`, the running script's live `bash -x` trace in `live_log`, and the current `setup_script`, `update_script` and `create_params`.
    `refresh_in_flight` is false once a refresh has run past its staleness window, even if `refresh_status` still says `refreshing`.
  - `rebuild`: start a refresh on a throwaway builder and return at once with `status: "started"` and a `task_id`; the image is not rebuilt yet.
    `kind="full"` (default) boots the base snapshot and runs `setup_script` then `update_script`, the nightly reproducibility check; `kind="update"` boots the current snapshot and runs only `update_script`.
    It takes minutes to an hour; follow it with `status` or `background_task("status", task_id)` at intervals, not in a loop.
    The image is replaced only if every script exits 0 and the capture succeeds, so a failed rebuild keeps the previous image.
    It is refused, with the running `task_id`, while another refresh runs.
  - `stop`: cancel the running refresh; the previous image stays in place.
  - `edit`: change `setup_script`, `update_script` and/or `create_params`, with the same validation as the dashboard's workspace settings.
    Only the fields passed change; `create_params: {}` clears them and an empty script removes it.
    The result carries `previous` values, so an edit can be undone by editing them back.
    An edit does not touch the current image: `rebuild` afterwards to build one from the new definition.
  - `history`: the workspace's audited changes from the last 30 days, newest first (at most 25): dashboard edits and rebuilds, `snapshotctl` and `publish_workspace` writes, settings changes, each with the actor's GitHub login and time.
- `workspace`: the workspace's name or slug; `list_workspaces` lists them.
- `kind`: `rebuild` only.
- `setup_script`, `update_script`, `create_params`: `edit` only.

To author an image by hand, provision the thread's sandbox and `publish_workspace` it instead; this tool never captures the calling sandbox.
Confirm with the user before `rebuild`, `stop` or `edit` on `default`: every unrouted run boots from it.
