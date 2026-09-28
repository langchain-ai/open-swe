List every distinct inbound event kind Open SWE received in the last two days, across GitHub, Slack, and Linear.

Each entry has `source`, `event_type`, `action` (empty when the payload has none), `count`, and `last_received_at`. Use it to pick `event_types` and `actions` for `listen_events`.
