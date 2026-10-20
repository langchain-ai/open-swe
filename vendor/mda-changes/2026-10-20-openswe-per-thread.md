# `_openswe_per_thread`: one box per thread whatever the options

**Verdict: not upstream as-is.** The behaviour is right; the option is the wrong shape for MDA.

**Changed:** `managed_deepagents/namespaces.py`, `sandbox.py` (`is_per_thread`), `runtime.py` (`_sandbox_scope_key`, `_create_langsmith_sandbox`).

## What

With `_openswe_per_thread=True`, a dynamic sandbox selection keeps the thread's one box: the selection's options (snapshot, sizing) apply only when MDA creates the box, never to choose a different one.

## Why

Open SWE declares the box per run from the thread's workspace: its snapshot, VM size and TTLs. A nightly snapshot refresh changes those options. MDA keys a dynamic selection's box by a fingerprint of its options, so the next run would get a new, empty box and strand the thread's uncommitted work.

## Upstream

MDA should never move a live thread to another box because a dynamic selection's options changed: new options apply to boxes it creates, and replacing a thread's box is an explicit act (see `replace_sandbox`). Then Open SWE stops passing the option.
