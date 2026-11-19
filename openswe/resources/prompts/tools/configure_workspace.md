Update routing for an existing compatibility workspace record.
The cloud Environments editor is removed; flat repository and Slack routing live in **Integrations** (`/admin/integrations`).

Requires a currently authorized workspace admin.
In a verified sole-writer shared thread, only an acknowledgement is returned; read existing values from a private admin surface.

- `workspace`: name or slug from `list_workspaces`.
- `changes`: only `repos`, `slack_channel_ids`, `kitchen_channel_ids` and `ask_channel_ids`.
  Omitted fields stay unchanged.
  Lists replace the entire list: read the existing configuration first and preserve entries not being changed.
- `slack_channel_ids` binds Slack channels to the workspace.
  `kitchen_channel_ids` is a subset of those bindings: every human top-level message in a Kitchen channel starts a thread, and replies continue it without a mention.
  `ask_channel_ids` enables the same delivery for bug-triage channels: reproduce reports, search related Linear issues and ongoing PRs, and recommend next steps without automatically tagging teammates.
  Suggest filing a Linear ticket or opening a PR, then wait for an explicit human choice; a bug report alone authorizes neither.
  Requested PRs are authored as the person choosing them, not Robin.
  Both lists must be subsets of the bound channels and cannot overlap.
  Newly enabled Kitchen or Ask channels must be internal Slack channels Robin has joined.
- Repository bindings are exclusive across compatibility records and route events; they do not grant access.
  With Robin-wide defaults enabled they do not control preloads or rebuild an image.
  With the toggle off, changing repositories can trigger a legacy snapshot rebuild.
- Legacy instructions, scripts, images, sizing, provider configuration and inheritance are not editable in either mode.
  Use `configure_execution_defaults` and the global image tools for Robin-wide execution changes.
- This tool changes routing bindings only, not behavioral settings or credentials.
  Use `manage_feature_flags` for supported boolean flags, `manage_review_approval_mode` for repository approval modes, and `manage_review_shadow` for the shadow review experiment.
  The default repository, organization guidelines, human-review auto-assignment delay, and all default model/reasoning-effort pairs remain dashboard-only settings.
  Manage credentials in the dashboard; never put them in workspace definitions.

Use this tool for an explicit request to change shared routing.
