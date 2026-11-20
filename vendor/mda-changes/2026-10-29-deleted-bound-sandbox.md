# Report a confirmed-deleted bound workspace as missing

**Verdict: upstream with adaptation.** A missing bound workspace needs a typed signal rather than an indistinguishable runtime error.

**Changed:** `managed_deepagents/runtime.py` (`_claim_langsmith_sandbox`) and `managed_deepagents/sandbox.py` (`BoundSandboxMissingError`).

A bound-name lookup returning provider `ResourceNotFoundError` raises `BoundSandboxMissingError`. Robin does not retry it and never creates a fresh workspace under the bound name: a thread and its sandbox are one. The backend asks LangSmith whether the bound sandbox is deleted, so it tells the person to start a new thread whichever error MDA raised. A review fails the same way. Other lookup failures and unhealthy live sandboxes also fail closed.

## Upstream

Shipped as `SandboxMissingError` with #67614 in `sdks/managed-deepagents`: MDA binds each thread to its sandbox and never recreates it. Drop this note when the vendored tree moves to that upstream. No recovery can restore files from the deleted workspace.
