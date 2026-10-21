# `_openswe_no_recover`: never recreate a box that disappears mid-run

**Verdict: not upstream as-is.** The behaviour is right for any box holding work MDA cannot rebuild; the option is the wrong shape for MDA.

**Changed:** `managed_deepagents/namespaces.py`, `sandbox.py` (`recovers`), `runtime.py` (`_LazyManagedSandboxBackend._recover`).

## What

With `_openswe_no_recover=True`, a sandbox call that finds the run's box gone fails as is. MDA does not create a fresh box under the same name and carry on.

## Why

Open SWE runs its file and shell tools in the thread's MDA box. That box holds the thread's only copy of its working tree, uncommitted changes included. A fresh box is empty, so recreating one mid-run would throw that work away while the run carried on as if it were still there. Open SWE's own sandbox code never does this: the run ends, the person is told the box stopped answering, and the next run decides what to do.

## Upstream

MDA should remove this recovery outright, for every project, not make it optional. Silently swapping an empty box in for a vanished one loses the agent's work while looking like success. A box that disappears mid-run should fail the call, and replacing it should be an explicit act (see `replace_sandbox`). Once MDA drops `_recover_managed_sandbox`, Open SWE stops passing the option.
