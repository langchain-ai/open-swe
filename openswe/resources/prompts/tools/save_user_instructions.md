Save the triggering user's standing instructions: their personal memory.

Do not call this by default. Call it only when the user asks you to remember
something for them personally ("remember this for me", "save it to my personal
memory", "from now on, when I say …"), whether a behavioural preference or a
fact about how they work, such as which repository they mean by a nickname. A
bare "remember this", or anything that could be meant for the whole team, is
not enough: ask whether they want it in their personal memory or as shared
guidance before calling this tool.

It is personal, not shared: only this user's future runs act on it, never
anyone else's.

Where it surfaces: as `standing_instructions` in this user's `person` context
block, in every thread they post in from any surface (Slack DMs, Slack
channels, the dashboard, GitHub). That includes shared contexts: in a thread
with other participants, or a public channel, everyone who can read the thread
can read it, even though it applies only to work done for this user's requests.
It does not reach threads this user never posts in, such as automations or
other people's threads. Never save anything sensitive here.

Do not save one-off task details or the current conversation, which belong to
this thread alone, or preferences about other users.

This is a full replacement: pass the COMPLETE new instruction text. Their
current text is the `standing_instructions` field of their `person` block;
preserve it and add the new item unless the user asked you to change or remove
something. Pass an empty string only when the user asks to clear their
instructions.

The user can also edit them in the dashboard under Settings → Instructions.

A person's block is sent when the thread starts and is not re-sent within this
run, so it keeps showing the old text after this call. The ``reminder`` in the
result is the current version — follow it for the rest of the thread.

Args:
    instructions: The complete user-level instruction text (markdown).

Returns:
    ``{"ok": True, "login": str, "instructions": str, "reminder": str}`` on
    success, or ``{"ok": False, "error": str}`` when the user could not be
    resolved.
