# Await worker eval cancellation

**Upstream as-is.**

`langchain_quickjs/_repl.py` waits for the worker's eval coroutine to unwind before propagating cancellation to its caller.
The cross-thread future becomes cancelled before the worker finishes, so closing the interpreter immediately afterward can close its Wasmtime store while cancellation handlers still run pending jobs.
Open SWE needs this ordering to evict cancelled invocations asynchronously without racing their evals.
Cancellation waits for worker unwinding; synchronous JavaScript can delay it until the configured per-call timeout.
The REPL also exposes `is_closed`, checking its context and engine state so recovery does not depend on Wasmtime's exception wording.
Drop this change once upstream waits for worker cancellation to finish before returning from `eval_async` and exposes closed-state detection.
