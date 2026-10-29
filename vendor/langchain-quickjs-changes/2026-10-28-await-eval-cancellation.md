# Await worker eval cancellation

**Upstream as-is.**

`langchain_quickjs/_repl.py` waits for the worker's eval coroutine to unwind before propagating cancellation to its caller.
The cross-thread future becomes cancelled before the worker finishes, so closing the interpreter immediately afterward can close its Wasmtime store while cancellation handlers still run pending jobs.
Open SWE needs this ordering to evict cancelled invocations asynchronously without racing their evals.
Cancellation waits for worker unwinding; synchronous JavaScript can delay it until the configured per-call timeout.
It waits only for an eval the worker started: a caller cancelled while the worker loop is busy cancels the worker task before its first step, and a coroutine cancelled before it starts never runs its `finally`, so waiting for it would hang.
The worker and the cancelled caller each take a lock first; the worker starts the eval only if the caller has not abandoned it, and the caller waits only if the worker started it.
The REPL also exposes `is_closed`, checking its context and engine state so recovery does not depend on Wasmtime's exception wording.
Drop this change once upstream waits for worker cancellation to finish before returning from `eval_async`, returns promptly when the worker never started the eval, and exposes closed-state detection.
