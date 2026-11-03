# Invocation-scoped async resources

**Upstream as-is.**

`run_resources.on_run_exit` registers async cleanup on the active managed invocation. `ManagedCompiledAgent.astream` owns its exit stack and closes it after the graph iterator has closed, including failures, cancellation and stream closure. The scope is entered only while advancing the iterator so it does not leak into the stream consumer.

Open SWE uses this to close code interpreter slots even when unrelated middleware fails or cancellation lands between graph steps, without tying their lifetime to temporary context objects or recoverable model errors.

Drop this change once MDA provides invocation-scoped async resource cleanup after graph tasks have unwound.
