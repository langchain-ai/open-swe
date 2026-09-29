Read or set server-backed feature flags at the instance level or for a workspace. Only available on a private admin surface to a currently authorized workspace admin. Use only for an explicit request to change shared flags; if personal versus shared scope is unclear, ask first.

- `action`: `read` or `set`. Read current flags before making a change if their existing values matter.
- `flags`: for `set`, a nonempty mapping of requested names to true, false, or null. Omit to read. Null clears the instance override to the deployment default, or restores inheritance from the instance for a workspace. Other settings remain unchanged.
- `workspace`: optional slug. Omit to affect the instance and all inheriting workspaces. A workspace must already exist.

Supported flags are the agent-manageable boolean fields in the shared workspace settings schema; use `read` to discover their names and values. Unknown fields, including credentials, model defaults, and approval automation, are rejected. Approval modes are managed per repository by `manage_review_approval_mode`; set `approve` only when explicitly requested. Personal flags are set with `save_user_settings`; browser-local flags cannot be changed by this tool.
