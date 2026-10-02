List every distinct inbound event kind Open SWE received in the last two days, across GitHub, Slack, and Linear.

Each entry has `source`, `event_type`, `action` (the payload's `action`, empty when it has none), `count`, and `last_received_at`. Use it to pick `event_types` and `payload_match` for `listen_events`.

Args:
    source: With `event_type`, narrows the list to that one event type and adds `payload_shape` to each entry: the newest payload of that kind with every value replaced by its JSON type (`string`, `number`, `boolean`, `null`), nested objects and the first element of each array kept. Build `payload_match` from the keys it shows.
    event_type: The event type to narrow to, together with `source`.
