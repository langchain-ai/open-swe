Listen for events and have each matching one start a run on this thread.

Only GitHub pull request events are supported for now: pull request updates, reviews, review comments, PR comments, and CI results (`check_run`, `check_suite`, `workflow_run`). Use `list_event_types` to see which event types and actions actually arrive. A subscription ends when it delivers the pull request's `closed` event, after its first wake when `one_shot` is set, when it expires, or when you cancel it. Events Open SWE itself caused, other than CI results, never wake the thread.

Prefer narrow filters: every matching event starts a run. For CI, `event_types=["check_suite"], actions=["completed"]` gives one wake per finished suite instead of one per check.

Args:
    action: `subscribe` creates a subscription and returns its `subscription_id`; `list` shows this thread's outstanding subscriptions with how often each has triggered; `cancel` removes the one named by `subscription_id`.
    pr_url: The GitHub pull request URL. Required for `subscribe`.
    subscription_id: The subscription to cancel. Required for `cancel`.
    event_types: GitHub event names to match, e.g. `pull_request_review`, `issue_comment`, `check_suite`. Empty matches every type.
    actions: Payload `action` values to match, e.g. `submitted`, `created`, `completed`. Empty matches every action.
    multitask_strategy: `enqueue` runs each event after the current run finishes; `interrupt` stops the current run and handles the event immediately.
    one_shot: End the subscription after the first event it delivers.
    instructions: What to do when woken, repeated in every wake message.
    expires_in_hours: How long to listen, 1 to 336 hours. Defaults to 168 (7 days).
