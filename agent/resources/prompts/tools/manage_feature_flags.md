Read or set server-backed feature flags at the instance level or for a workspace. Only available on a private admin surface to a currently authorized workspace admin. Use only for an explicit request to change shared flags; if personal versus shared scope is unclear, ask first.

- `action`: `read` or `set`. Read current flags before making a change if their existing values matter.
- `flags`: for `set`, a nonempty mapping of requested names to true, false, or null. Omit to read. Null clears the instance override to the deployment default, or restores inheritance from the instance for a workspace. Other settings remain unchanged.
- `workspace`: optional slug. Omit to affect the instance and all inheriting workspaces. A workspace must already exist.

Supported flags: `review_draft_prs`, `pr_summaries`, `review_trace_links`, `model_routing_enabled`, `gateway_enabled`, `fable_enabled`, `expedited_review_enabled`. Fable's model safety policy is applied when changing `fable_enabled`. The approval automation toggle is managed separately by `manage_review_approval_policy` and must only be enabled when explicitly requested. Personal experimental assistant UI and sandbox memory flags are set with `save_user_settings`; the Feature Flags tab visibility is browser-local and cannot be changed by this tool.
