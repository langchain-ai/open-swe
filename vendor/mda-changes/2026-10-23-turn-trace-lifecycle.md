Upstream as-is.

The Python runtime's `_run_input.py` uses a per-invocation LangChain tracer to record bounded root end reasons, treat explicit multitask supersedes as expected cancellation, close unfinished child spans at root termination, and suppress late child recording. Tracer copies share the invocation lifecycle while preserving the requested tracing destinations and external parents. Open SWE needs this because Agent Server can close a stream while model and tool work is still unwinding; successful-run middleware cannot observe those exits.

The deploy CLI in `crates/mda/src/deploy/mod.rs` sets `LANGSMITH_REVISION_ID` from the project's checked-out git commit, replacing stale environment values. This identifies the deployed runtime independently of the backend's revision and the deployment's UUID. Projects outside git retain their explicitly configured revision.

Upstream: drop these edits once MDA provides equivalent trace lifecycle handling and git revision propagation for Python deployments.
