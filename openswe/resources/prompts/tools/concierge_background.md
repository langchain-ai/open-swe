Read or change the standing instructions of this person's concierge background thread.

The background thread works for the person between their messages: it is woken every 30 minutes with their local time and these instructions, and reports back to you.
Call this with no arguments to see its thread id, its current instructions and the person's timezone.
When the person tells you how the background work should go ("check my PRs every morning", "do everything for me", "stop pinging me after 6pm"), save instructions that capture all of it: they replace the previous ones, so carry over what still applies.
An empty string restores the defaults.

To give the background thread a one-off task or ask how it is going, message it with `manage_thread` and read it with `get_thread`, using the thread id this returns.

Args:
- instructions: The new standing instructions, written to the background thread in the second person.
  Omit to read without changing anything.

Returns ``ok``, ``thread_id`` (``null`` until the first check-in starts the thread), ``instructions``, ``custom_instructions`` and ``timezone``.
