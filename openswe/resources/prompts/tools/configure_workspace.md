Update an existing workspace's definition without capturing or rebuilding the calling sandbox.

Requires a currently authorized workspace admin.
In a verified sole-writer shared thread, only an acknowledgement is returned; read existing values from a private admin surface.

- `workspace`: name or slug from `list_workspaces`.
- `changes`: workspace definition fields to change, using the same schema and validation as the dashboard.
  Omitted fields stay unchanged.
  Lists replace the entire list: read the existing configuration first and preserve entries not being changed.
- `slack_channel_ids` binds Slack channels to the workspace.
  `kitchen_channel_ids` is a subset of those bindings: every human top-level message in a Kitchen channel starts a thread, and replies continue it without a mention.
  Newly enabled kitchens must be internal Slack channels Open SWE has joined.
  `bug_report_kitchens` maps a bound channel that is not a kitchen to one of the workspace's kitchen channels, and replaces the whole mapping when given: Open SWE classifies each new top-level human message in that channel and starts a thread in the kitchen for every bug report, acting for its author.
  Both channels of a newly added route must be public and joined by Open SWE.
- Repository bindings are exclusive across workspaces.
  Changing repositories can trigger a snapshot rebuild; other definition edits do not capture a sandbox.
- Prompt, scripts, sizing, snapshot settings and sandbox inheritance use the dashboard's existing rules.
  Use `snapshotctl` for rebuilds and `publish_workspace` only to capture a provisioned sandbox.
- This tool changes workspace definitions only, not behavioral settings or credentials.
  Use `manage_feature_flags` for supported boolean flags, `manage_review_approval_mode` for repository approval modes, and `manage_review_shadow` for the shadow review experiment.
  The default repository, organization guidelines, human-review auto-assignment delay, managed-tools gateway ID, and all default model/reasoning-effort pairs remain dashboard-only settings.
  Manage credentials in the dashboard; never put them in workspace definitions.

Use this tool for an explicit request to change shared workspace configuration.
Confirm changes to the default workspace's prompt or provisioning before applying them.
Do not use a workspace prompt to bypass authorization or approval safeguards.
