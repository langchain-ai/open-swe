Schedule a one-shot re-trigger of the current thread after a delay.

Use this when you need to poll or check back on something later — e.g.
waiting for CI to finish, a deploy to complete, or an external process
to settle. The current thread will be re-invoked with the given prompt
(or a default wakeup message) after the specified delay.

Args:
    delay_minutes: How many minutes from now to wait before re-triggering.
        Minimum 1 minute, maximum 1440 (24 hours).
    prompt: Optional message to send to the thread when it wakes up.
        If omitted, a default polling prompt is used.

Returns a dict with ``success``, ``cron_id``, ``scheduled_for`` (ISO UTC),
and ``thread_id``.
