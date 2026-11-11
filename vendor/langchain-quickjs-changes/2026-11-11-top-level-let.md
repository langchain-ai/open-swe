# Reuse top-level let bindings

**Verdict: upstream as-is.** Persistent REPL bindings should be reusable without a failed eval and a model retry.

## What

`_repl.py` rewrites conflicting top-level `let` identifier declarations into assignments, preserving other declarations and nested scopes.
An execution marker permits recovery only for a matching declaration error before the script starts, including multiple conflicts in one eval.
An isolated context checks the source with an unconditional throw before its body, so declaration conflicts within the same script remain syntax errors without executing user code.
Errors after execution starts are surfaced without retrying, including errors from nested evaluation, and a recovered script's own failure is preserved.
The existing `TOP_LEVEL_CONST_TO_VAR` runtime policy remains unchanged.
The JavaScript parser identifies declaration tokens and source spans; its two dependencies already occur in the runtime dependency graph and are now direct dependencies of the package that uses them.
The persistence guidance and README describe this policy.

## Why

Robin reuses variable names across code-mode calls in the same persistent context.
Handling those conflicts before execution saves a model retry without replaying tool calls or discarding persistent state.
Fresh and nested declarations retain lexical semantics; recovery conservatively leaves unsupported binding patterns as visible errors.

## Upstream

Drop this change once upstream supports safely reusing persistent top-level `let` bindings alongside its existing top-level `const` transformation.
