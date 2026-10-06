Listen for inbound events in this thread's workspace and have each matching one delivered to this thread.

Events come from GitHub (repositories bound to the workspace) and Slack (channels bound to the workspace). Only Slack events from verified public channels are published to the event log and subscriptions. Private channels, DMs, and events whose channel cannot be verified as public are excluded, including this thread's own channel. Use `list_event_types` to see which sources and event types actually arrive, and `list_event_types(source=..., event_type=...)` to see a payload's shape before writing `payload_match`. Events Open SWE itself caused never match, except GitHub CI results.

Every matching event is delivered exactly once, oldest first, as a message from the event listener. None is lost when the thread is busy or when a run is interrupted: if a run is stopped before an event reached it, the next run on this thread receives that event before anything newer. A subscription ends after its first match when `one_shot` is set, after it delivers a subscribed pull request's `pull_request.closed` event, when it expires, or when you cancel it.

Prefer narrow filters: each matching event costs a model turn. For CI, `event_types=["check_suite.completed"]` gives one event per finished suite instead of one per check, and adding `payload_match={"check_suite": {"conclusion": "failure"}}` only the failed ones.

Args:
    action: `subscribe` creates a subscription and returns its `subscription_id`; `list` shows this thread's outstanding subscriptions, each with `trigger_count` (events matched) and `last_triggered_at`; `cancel` removes the one named by `subscription_id`.
    sources: `github` and/or `slack`. Empty matches every source.
    repo: A GitHub repository (`owner/name`) of this workspace; only its events match.
    pr_url: A GitHub pull request URL; only that pull request's events match (reviews, comments, pushes, CI).
    event_types: Event types to match, as `list_event_types` names them. A bare name matches every action: `pull_request` matches `pull_request.opened`, `pull_request.closed`, and the rest; `pull_request.closed` matches only that action. E.g. GitHub `pull_request_review.submitted`, `issue_comment`, `check_suite.completed`, `issues.opened`; Slack `message`, `reaction_added`. Empty matches every type. `subscribe` needs at least one of `event_types`, `repo`, or `pr_url`.
    payload_match: A JSON object the event's payload must contain. Every key you give must be present with the same value; nested objects match the same way, and an array matches when it contains every element you list. Other keys in the payload are ignored. Examples: `{"review": {"state": "changes_requested"}}`, `{"label": {"name": "bug"}}`, `{"event": {"channel": "C0123ABC"}}`. For "either A or B", create two subscriptions. Empty matches every payload.
    when_busy: What happens when a matching event arrives while this thread is already running, including the run that is subscribing right now.
        `wait` (default): nothing is interrupted. The event is added to the running thread just before its next model call, so you see it within one step of your current work; if the run is already finishing, a new run starts with it right after. Use this for almost everything: review comments, CI results, new issues.
        `interrupt`: the current run is stopped immediately and a new run starts with the event. Work already completed in the stopped run is kept; the step in progress is abandoned, so a tool call that was running returns as cancelled and its sandbox command is killed. Events that were waiting are delivered first, then this one. Use it only when the event makes the current work pointless, for example the pull request you are fixing was closed.
    one_shot: End the subscription after the first event it matches.
    instructions: What to do when an event arrives; repeated with every event from this subscription.
    expires_in_hours: How long to listen, 1 to 336 hours. Defaults to 168 (7 days).
    subscription_id: The subscription to cancel. Required for `cancel`.
