Patch one typed Robin settings resource through dashboard validation and writers; never execute SQL.

Use `read_settings(resource=..., schema_only=true)` to discover every supported field.
Admins may change all global Defaults fields (`instance`), execution defaults (`execution`), repository opt-in/approval/full styles/instructions, and personal settings.
Repository resources require `repository="owner/name"`; `user_id` selects another person for admins only.

Nonadmins may change only their own `profile`, `preferences`, `user_preferences` and `instructions` when the trusted run context sets `personal_settings_tools_enabled` to true (default off).
It is not a persisted admin setting and cannot be enabled through this tool.
Admins do not need this parameter enabled.
Existing ordinary personal-settings tools are unaffected.

- Supply a nonempty patch containing only schema fields.
  Lists/objects replace that field; omitted fields remain unchanged.
  Global null restores built-in defaults.
- Global, execution, review opt-in and review-style writes require the latest `expected_version` from a private read and reject concurrent changes atomically.
  Re-read before retrying; never guess versions or fall back to an unversioned write.
- Omit `expected_version` for personal settings and repository instructions; these use existing validated partial writers.
- Shared writes require a verified sole writer and return only an acknowledgement; a second writer revokes access.
  Reads stay private.
- Saving execution defaults does not publish an image, recreate a sandbox or change routing.
  Never do those without the corresponding request.
