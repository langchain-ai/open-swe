Save the triggering user's standing instructions: their personal memory.

Use this whenever the user asks you to remember something for their future
runs: a behavioural preference ("always …", "never …", "from now on …"), or
a fact about how they work, such as which repository they mean by a nickname
("the GTM agent repo is `langchain-ai/ai-sdr`"). It is personal, not shared:
only this user's future runs use it, and nobody else's. If the user wants
guidance for everyone, or personal versus shared scope is unclear, ask before
calling this tool.

Where it surfaces: as `standing_instructions` in this user's `person` context
block, in every thread they post in from any surface (Slack, the dashboard,
GitHub). It applies only to work done for their requests, never to other
people's messages, though anyone else in that thread can read it. It does not
reach threads this user never posts in, such as automations or other people's
threads.

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
