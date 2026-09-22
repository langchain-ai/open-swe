Update the authenticated requester's ordinary personal settings in a private dashboard thread or linked Slack DM.

Pass only the fields the user asked to change in `settings`; omitted fields stay unchanged. No login, email, thread ID, or on-behalf-of identity is accepted. Use `read_user_settings` to inspect existing values before changing model/effort pairs. Model choices and reasoning-effort combinations use the same validation and stale-model normalization as the dashboard.

Supported profile fields:
- `default_model`, `reasoning_effort`: the main-agent model and effort.
- `default_subagent_model`, `subagent_reasoning_effort`: subagent overrides; set both to null to inherit the main model.
- `default_repo`, `base_branch`, `branch_prefix`: repository/branch defaults; null clears them.
- `auto_fix_ci`: boolean preference.
- `slack_onboarding_dismissed`: boolean; true hides the automatic Slack connection prompt across devices.
- `model_routing_enabled`, `review_draft_prs`: boolean overrides; null inherits the shared default.
- `draft_prs`: whether newly opened PRs are drafts; use true or false (null keeps the existing preference).

Supported dashboard preferences:
- `default_visibility`: `private` or `public`, for future threads only.
- `default_workspace`: workspace slug; null clears the default.
- `local_tracing_project`: tracing project; null clears it. Restart the desktop app after changing this.
- `transcript_streaming`: boolean; reopen a thread after changing this.

`dm_session_enabled` is read-only through agent tools. Direct the user to the dashboard settings to enable or disable it; including it rejects the entire patch without saving any fields.

Only save explicitly requested personal changes. If personal versus shared scope is unclear, ask first. Use `save_user_instructions` for standing behavioral guidance and the existing personal skill tools for skills. This tool cannot change credentials, account links, admin/shared settings, or browser-local appearance/notification preferences. Defaults apply to future runs/threads; do not claim to have changed the current run's model, thread visibility, or workspace.

Returns the requester login and the updated fields, or a validation/authorization error. All fields are validated before saving; a storage failure may leave a partial save across the two settings records, so read back before retrying.
