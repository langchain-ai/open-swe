# `_openswe_handles_tool_errors`: no MDA tool error middleware around Open SWE's tools

**Verdict: not upstream as-is.** It switches MDA's tool error handling off for an agent rather than letting the author name the errors that end a run.

**Changed:** `managed_deepagents/_tool_errors.py` (`handles_tool_errors`), `runtime.py` (`_prepend_tool_error_middleware`, and the subagents' middleware). Open SWE sets it on `FileToolsMiddleware` in `mda/rt/file_tools.py`.

## What

MDA 0.11 puts a tool error middleware outside every agent's own, which returns any tool error to the model except a few kinds of its own. When an agent's middleware has `_openswe_handles_tool_errors = True`, MDA adds none there.

## Why

Open SWE already returns tool errors to the model where the model can act on them (`rt.tool_errors`). The ones that escape its middleware are meant to end the run, once the backend has told the person why: a sandbox that never booted or stopped answering, or a proxy config MDA couldn't write. MDA's middleware turned those into tool results, so the model kept calling tools against a sandbox that could never answer.

## Upstream

MDA should let an author name the errors that end a run, for example with a base class its tool error middleware re-raises. Open SWE then raises those and drops this option.
