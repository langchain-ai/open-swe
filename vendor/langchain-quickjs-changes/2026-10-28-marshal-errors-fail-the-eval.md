# Marshal errors fail the eval, not the run

**Upstream as-is.**

`langchain_quickjs/_repl.py` reports a `MarshalError` that escapes an eval as the eval's error (`error_type` `MarshalError`), as it already does for `MemoryLimitError` and `Timeout`.
When a tool called from code returns a value the guest cannot take, such as a result too large or too deeply nested, quickjs-rs rejects that call's Promise and, if the JavaScript leaves it uncaught, re-raises the original `MarshalError` out of `eval_handle_async`.
Nothing caught it there, so LangGraph's tool node re-raised it and the whole run ended with an unexpected error, as a LangSmith `query_runs` call that asked for 100 runs with full inputs and outputs did in production.
The interpreter is unharmed, so its state is kept and the model can retry with a smaller request.
Drop this change once upstream catches `MarshalError` around the eval itself, not only around converting its result.
