# Never recreate a box that disappears mid-run

**Verdict: upstream as-is.**

**Changed:** `managed_deepagents/runtime.py` (`_recover_managed_sandbox`).

## What

When a sandbox call finds the run's box gone, the call fails. MDA no longer creates a fresh, empty box under the same name and carries on. It still adopts a box another process has already replaced under that name, as `replace_sandbox` does.

## Why

A thread's box holds the only copy of its files, uncommitted work included. Swapping in an empty box loses all of that while the run carries on as if nothing happened. If the sandbox dies, the run dies, and the person decides what happens next.

## Upstream

Propose this change to MDA as is, with no option, opt-in or otherwise. Drop ours once `main` has it.
