# Upstream as-is.

`crates/mda/src/compile/mod.rs` moves the default Agent Server from `0.19.0rc1` to `0.19.0rc6` (`LANGGRAPH_API_VERSION`), and the paired in-memory runtime from `0.39.0rc1` to `0.39.0rc6` (`LANGGRAPH_RUNTIME_INMEM_VERSION`), with the test that pins the default. It replaces the `0.19.0rc1` from [2026-11-13-langgraph-api-0-19-0rc1.md](2026-11-13-langgraph-api-0-19-0rc1.md), and `0.19.0rc6` keeps that release's `select` on `GET /threads/{thread_id}/state`.

Robin needs it for faster thread opens. `0.19.0rc6` is the first release whose DeltaChannel history read, which rebuilds `messages` on every state read, fetches only the checkpoints back to the last snapshot instead of a fixed 1,024 (langchainplus#67929, cherry-picked to rc as #67999).

Upstream: drop this once upstream's `LANGGRAPH_API_VERSION` default is `0.19.0rc6` or later.
