Save the triggering user's standing, user-level custom instructions.

Call this only when the user explicitly says a standing behavioural preference
("always …", "never …", "from now on …", "stop doing …") is personal to
them and should apply to all their future runs. If personal versus shared/global
scope is unclear, ask the user which scope they intend before calling this tool.

This is not general-purpose memory. Do not use it for one-off task details,
conversation context, shared team/repository/project facts, or preferences
concerning other users.

This is a full replacement: pass the COMPLETE new instruction text. Your
current user-level instructions are shown in your system prompt under "Your
Custom Instructions (user-level)"; preserve those lines and add the new rule
unless the user asked you to change or remove something. Pass an empty string
only when the user asks to clear their instructions.

The user can also edit them in the dashboard Profile tab.

A thread's system prompt is fixed when the thread opens, so it keeps showing
the old text after this call. The ``reminder`` in the result is the current
version — follow it for the rest of the thread.

Args:
    instructions: The complete user-level instruction text (markdown).

Returns:
    ``{"ok": True, "login": str, "instructions": str, "reminder": str}`` on
    success, or ``{"ok": False, "error": str}`` when the user could not be
    resolved.
