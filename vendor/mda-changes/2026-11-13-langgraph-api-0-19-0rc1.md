# Upstream as-is.

`crates/mda/src/compile/mod.rs` moves the default Agent Server from `0.18.0rc5` to `0.19.0rc1` (`LANGGRAPH_API_VERSION`), and the paired in-memory runtime from `0.38.0rc5` to `0.39.0rc1` (`LANGGRAPH_RUNTIME_INMEM_VERSION`), with the test that pins the default. It replaces the `0.18.0rc5` from [2026-11-10-langgraph-api-0-18-0rc5.md](2026-11-10-langgraph-api-0-18-0rc5.md). `0.19.0rc1` keeps that note's Redis fix: it has langchainplus#61261, the `main` change that #61425 cherry-picked.

Robin needs it for `select` on `GET /threads/{thread_id}/state` (langchainplus#57809), which useStream's `stateKeys` sends so that opening a thread loads only the state keys Robin's UI reads, not the 100–135 KB of `open_swe_state`, `open_swe_call` and `skills_metadata` on every thread. `0.18.x` ignores `select` on `/state` and returns the full state. `0.19.0rc1` is the first published release with it.

`crates/mda/src/dev/mod.rs` also has `mda dev` install `langgraph-api[inmem]` rather than `langgraph-api`, with the test that checks its arguments. From `0.19.0rc1`, `langgraph dev` runs on the Go core by default (langchainplus#56246). The core ships in `langgraph-api-core-bin`, which only that extra installs, and without it the server won't start.

Upstream: drop this once upstream's `LANGGRAPH_API_VERSION` default is `0.19.0rc1` or later and its `mda dev` installs `langgraph-api[inmem]`.
