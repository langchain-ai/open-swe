Read server-backed settings for the authenticated owner of a private thread, or redacted settings for verified participants in a collaborative thread.

In a private thread this includes all ordinary profile and dashboard preferences,
including repository/branch defaults, model routing, visibility, workspace,
local tracing, transcript streaming, and follow-up behavior. Only the authenticated owner is read;
other participant identities cannot broaden private settings access. Use
`save_user_settings` for requested personal setting changes and
`save_user_instructions` for standing behavioral guidance.

This tool accepts no user, thread, or source identifiers. It derives the active
thread from trusted runtime context and returns only mapped human participants.
Connection data is redacted status metadata; credentials and tokens are never
returned. Browser-local theme and notification preferences are not server-backed.
