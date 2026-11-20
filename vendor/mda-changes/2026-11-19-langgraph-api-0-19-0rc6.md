# Upstream as-is.

`crates/mda/src/compile/mod.rs` moves the default Agent Server from `0.19.0rc1` to `0.19.0rc6` (`LANGGRAPH_API_VERSION`), and the paired in-memory runtime from `0.39.0rc1` to `0.39.0rc6` (`LANGGRAPH_RUNTIME_INMEM_VERSION`), with the test that pins the default. MDA 0.11's default is `0.19.0rc1`, which has langchainplus#61261's Redis fix and `select` on `GET /threads/{thread_id}/state` (langchainplus#57809), which useStream's `stateKeys` sends so that opening a thread loads only the state keys Robin's UI reads; `0.19.0rc6` keeps both.

`crates/mda/src/dev/mod.rs` also has `mda dev` install `langgraph-api[inmem]` rather than `langgraph-api`, with the test that checks its arguments. From `0.19.0rc1`, `langgraph dev` runs on the Go core by default (langchainplus#56246). The core ships in `langgraph-api-core-bin`, which only that extra installs, and without it the server won't start.

Robin needs it for faster thread opens. `0.19.0rc6` is the first release whose DeltaChannel history read, which rebuilds `messages` on every state read, fetches only the checkpoints back to the last snapshot instead of a fixed 1,024 (langchainplus#67929, cherry-picked to rc as #67999).

Upstream: drop this once upstream's `LANGGRAPH_API_VERSION` default is `0.19.0rc6` or later and its `mda dev` installs `langgraph-api[inmem]`.
