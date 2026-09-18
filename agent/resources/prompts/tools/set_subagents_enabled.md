Enable or disable subagents for the triggering user's future runs.

Call this only when the triggering user explicitly asks to change their own
subagent preference. The setting is user-level and applies to future runs; it
does not reconfigure the active run. This tool resolves the user from trusted
runtime context and cannot modify another user's profile.

Args:
    enabled: Whether future runs should include subagents.

Returns:
    ``{"ok": True, "login": str, "subagents_enabled": bool}`` on success, or
    ``{"ok": False, "error": str}`` when the user cannot be resolved or the
    preference cannot be saved.
