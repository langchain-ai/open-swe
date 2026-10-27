# Agent Server dev release

**Not upstream as-is.** Open SWE opts into a development build rather than changing MDA's release policy.

- **What:** Pin `LANGGRAPH_API_VERSION` to `0.18.0.dev9` and its paired in-memory runtime to `0.38.0.dev9` in `crates/mda/src/compile/mod.rs`.
- **Why:** MDA deployment images need the fix restoring run cancellation after Redis Cluster control subscriptions drop. Updating the backend's `pyproject.toml` does not change MDA's compiled image pin.
- **Upstream:** Drop this override once MDA's default Agent Server release includes that fix.
