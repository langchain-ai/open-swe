# `_openswe_thread_id`: work in another thread's sandbox

**Verdict: not upstream as-is.** It names another thread's sandbox without MDA checking that the run may use it.

**Changed:** `managed_deepagents/namespaces.py`, `sandbox.py` (`owning_thread`), `runtime.py` (`_sandbox_scope_key`, `_create_langsmith_sandbox`).

## What

`_openswe_thread_id=<thread>` makes the run use that thread's sandbox instead of its own.

## Why

An Open SWE task worker works in its coordinator's sandbox. Open SWE's backend checks ownership, task membership and repository scope before it names the coordinator (`ManagedSandbox.for_thread`, `_attach_task_worker_sandbox`), so the option is safe as Open SWE uses it, but MDA itself checks nothing.

## Upstream

MDA should let a run share another thread's sandbox only once it has verified the caller may: the same caller owns both threads, or MDA itself linked the threads, as it does an async subagent to its lead. Then Open SWE links worker threads that way and drops the option.
