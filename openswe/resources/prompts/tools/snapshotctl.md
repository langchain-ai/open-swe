Inspect a retained legacy workspace image or stop its running automatic refresh.
Use `configure_execution_defaults`, `read_execution_image` and `rebuild_execution_image` for Robin-wide execution changes; do not delete rollback images.
The cloud Environments editor is removed regardless of the toggle.
Workspace admins only.
`status` and `history` require a private admin surface (a private dashboard thread, an authenticated Slack DM, or MCP); the writes also work in an admin thread or a verified sole-writer shared thread, returning only an acknowledgement there.
Every write is recorded in the audit log under the acting admin.

- `action`:
  - `status`: the snapshot runs boot from (`snapshot_id`, `snapshot_status`, `last_captured_at`), the latest refresh (`refresh_status`, `refresh_kind`, `refresh_steps` with each stage's exit code, `refresh_error`, `refresh_task_id`), the end of its log in `refresh_log_tail`, the running script's live `bash -x` trace in `live_log`, and the current `setup_script`, `update_script` and `create_params`.
    `refresh_in_flight` is false once a refresh has run past its staleness window, even if `refresh_status` still says `refreshing`.
  - `stop`: cancel the running refresh; the previous image stays in place.
  - `history`: the workspace's audited changes from the last 30 days, newest first (at most 25): dashboard edits and rebuilds, historical environment publications, `snapshotctl` writes and settings changes, each with the actor's GitHub login and time.
- `workspace`: the workspace's name or slug; `list_workspaces` lists them.

This tool never captures the calling sandbox.
Manual publication is available only for the Robin-wide image, with Robin-wide defaults enabled, through `publish_execution_image`.
Confirm with the user before `stop` on `default`: every unrouted run boots from it.
