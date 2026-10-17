List every distinct event type Open SWE logged in the last two days: inbound GitHub, Slack, and Linear deliveries, and Open SWE's own decisions under source `openswe`.

Each entry has `source`, `event_type`, `count`, and `last_received_at`. An event whose payload has an `action` is named `<event>.<action>`, e.g. `pull_request.opened` or `check_suite.completed`; one without keeps the bare name, e.g. `push`. Open SWE's decisions are named `<area>.<decision>`, e.g. `human_review.reviewers_released`. Use it to pick `event_types` and `payload_match` for `listen_events`.

Args:
    source: Narrows the list to one source. With an exact `event_type`, also adds `payload_shape` to that entry: its newest payload with every value replaced by its JSON type (`string`, `number`, `boolean`, `null`), nested objects and the first element of each array kept. Build `payload_match` from the keys it shows.
    event_type: Narrows the list to that event type, and to all its actions when given without one: `pull_request` lists every `pull_request.*` type.
