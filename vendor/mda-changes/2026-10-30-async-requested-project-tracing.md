Upstream as-is.

- **What:** `_run_input.py` recognizes the requested project in the marked tracing headers deepagents sends when launching an async subagent. Requested-project-only runs select that project even when the worker has no matching replica, while preserving matching replica credentials and the launching span as parent.
- **Why:** Open SWE's async workers otherwise keep deployment-project replicas or fall back to that project despite receiving the coordinator's trace headers, splitting a turn across projects.
- **Upstream:** Drop this patch once MDA applies its requested-project tracing policy to async subagent launches, including runs without a matching replica. The default replication policy and runs without a requested project remain unchanged.
