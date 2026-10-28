Upstream as-is.

The Python runtime's `_run_input.py` uses a per-invocation LangChain tracer to record bounded root end reasons, treat explicit multitask supersedes as expected cancellation, close unfinished child spans at root termination, and suppress late child recording. Tracer copies share the invocation lifecycle while preserving the requested tracing destinations and external parents. Open SWE needs this because Agent Server can close a stream while model and tool work is still unwinding; successful-run middleware cannot observe those exits.

Upstream: drop these edits once MDA provides equivalent trace lifecycle handling for Python deployments.
