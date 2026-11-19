List distinct inbound event types Robin received in the last 14 days, within the thread's authorized scope.

Non-admin threads are restricted to their saved repository after a verified GitHub read-access check; without a saved repository, discovery is refused.
Only authorized admin threads can discover events globally across repositories and services.
Counts and the newest payload shapes use the same repository and visibility scope.
Event visibility is enforced per item: public by default, private only in its user’s private thread, and admin-only only in an authorized admin thread.

Admin threads also see `robin` events for Robin-generated activity, such as `task.suggested`.
Track global image builds with `background_task`; use the observed event catalog rather than assuming a refresh event is published.

Admin threads also see `reviewer` events: `run.started`, `run.completed`, `run.failed`, `run.canceled`, `finding.created`, `finding.updated`, `review.published`, and `review.publish_failed`.
Findings include their stored content, status, location, publication identities, and changed fields.
Runs carry the thread and run IDs; all events link the pull request.

Admin threads also see `human_review` events for request creation and changes, assignment and agent wakes, GitHub review requests, notification delivery outcomes, snoozes and declines, supplementary reviews, and scheduled/fired deadlines.
Payloads include the request ID and PR URL; run configuration and DM bodies are excluded.

Each entry has `source`, `event_type`, `count`, and `last_received_at`.
An event whose payload has an `action` is named `<event>.<action>`, e.g. `pull_request.opened` or `check_suite.completed`; one without keeps the bare name, e.g. `push`.
Use it to pick `event_types` and `payload_match` for `listen_events`.

Args:
- source: Narrows the list to one source.
  With an exact `event_type`, also adds `payload_shape` to that entry: its newest payload with every value replaced by its JSON type (`string`, `number`, `boolean`, `null`), nested objects and the first element of each array kept.
  Build `payload_match` from the keys it shows.
- event_type: Narrows the list to that event type, and to all its actions when given without one: `pull_request` lists every `pull_request.*` type.
