# Upstream as-is.

`crates/mda/src/compile/mod.rs` moves the default Agent Server from `0.18.0.dev12` to `0.18.0rc5` (`LANGGRAPH_API_VERSION`), and the paired in-memory runtime from `0.38.0.dev12` to `0.38.0rc5` (`LANGGRAPH_RUNTIME_INMEM_VERSION`), with the test that pins the default.

Robin needs it because `0.18.0.dev12` predates langchainplus#61425. On those builds, Redis never clears a pub/sub subscriber's soft output-buffer timer once its buffer drains, so a large state event more than a minute after another disconnects every live stream subscriber on the thread. That is behind `open-swe-mda-prod`'s stream event loss alerts. `0.18.0rc5` is the first published release with the fix.

Upstream: drop this once upstream's `LANGGRAPH_API_VERSION` default is `0.18.0rc5` or later.
