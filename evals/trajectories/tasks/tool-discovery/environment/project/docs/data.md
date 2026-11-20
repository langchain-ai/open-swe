# Where data lives

- `exports/billing_events/`: raw billing events, one JSON object per line, sharded and gzipped.
  Unfiltered: every workspace, every event type.
- The analytics warehouse: curated tables, including the finance metrics.
  Query it with the `warehouse` CLI (`warehouse tables`).
