Replace this admin thread's sandbox using a complete create request.

Every supplied argument is forwarded to the LangSmith sandbox-create body.
The schema includes all public create fields and accepts additional hidden
fields such as ``_internal_runtime``. Omitted fields use platform defaults.
The new sandbox starts empty except for any requested snapshot, and the old
sandbox is preserved but detached from this thread. Never pass secrets,
credentials, or authentication tokens.
