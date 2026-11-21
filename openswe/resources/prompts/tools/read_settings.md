Read any typed Robin settings resource and its schema; never execute SQL.

- Admin resources: `instance` (every global Defaults setting), `execution` (instructions, repos, images, resources, scripts and cloud/proxy configuration), `repository_reviews`, `repository_approval`, `repository_style` (full review style) and `repository_instructions`.
  Repository resources require `repository="owner/name"` and recheck repository access.
- Personal resources: `profile` (Git, review and profile preferences), `preferences` (thread defaults and Changes panel), `user_preferences` (concierge, kitchen and experiments), and `instructions`.
  Nonadmins may read only themselves when the trusted run context enables `personal_settings_tools_enabled` (default off); this is not a persisted admin setting or a tool argument.
  Admins may select another person by immutable `user_id`.
  Credentials and identity records are never settings fields.
- `feature_flags`, `review_shadow` and `review_apps` remain subset aliases for global settings.
  There are no workspace overrides.
- `schema_only=true` discovers the typed patch schema; authorization still applies.
  Values for global/execution settings are stored overrides, not effective defaults.
  Null resets a global setting to its built-in default.
- Global, execution, review opt-in and review-style reads return a `version` for atomic `patch_settings` writes.
  Personal settings and repository instructions use their existing validated partial writers without version tokens.
- Reads require a private owner surface; shared sole-writer threads never grant read access.
  Global, execution, repository and other-person reads additionally require a private admin surface.
  Continue privately for sensitive values and versions.
