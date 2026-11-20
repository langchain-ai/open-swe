# `_openswe_sandbox_id`: the sandbox name Open SWE's backend recorded

**Verdict: not upstream as-is.** It carries Open SWE's own record of a thread's sandbox into MDA's binding.

**Changed:** `managed_deepagents/runtime.py` (`_claim_langsmith_sandbox`, `_persisted_or_none`), `sandbox.py` (`RESOLUTION_OPTION_KEYS`).

## What

`define_sandbox(_openswe_sandbox_id=name)` makes `name` the only sandbox the thread may open. MDA treats it as bound, as if the thread's `ls_mda_sandbox_binding` metadata named it: it adopts that sandbox and records the binding, and if LangSmith confirms the sandbox is gone it raises `SandboxMissingError` instead of creating one. MDA's own name for the thread is never tried, and a sandbox this process cached under another name for the thread is ignored.

## Why

Open SWE's backend created the sandboxes of threads that predate MDA owning them, under names MDA's formula doesn't produce (including suffixed ones from old splits), and it records each thread's sandbox. Those threads must keep working in the sandbox that holds their work.

## Upstream

MDA should let a caller seed a thread's binding, for example by writing `ls_mda_sandbox_binding` itself or by letting `define_sandbox(resolve=...)` name the sandbox. Once it can, Open SWE writes its recorded name there and drops this option. `resolve` (langchain-ai/langchainplus#68137) picks a run's create options and proxy config, not which sandbox it adopts, so Open SWE's resolver still passes the recorded name as `_openswe_sandbox_id`, which reaches the run's resolved sandbox with its other options.
