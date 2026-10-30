# Recover a confirmed-deleted bound workspace

**Verdict: upstream with adaptation.** Missing bound workspaces need a typed recovery signal rather than an indistinguishable runtime error.

**Changed:** `managed_deepagents/runtime.py` and `managed_deepagents/sandbox.py`.

A bound-name lookup returning provider `ResourceNotFoundError` raises `BoundSandboxMissingError`. Open SWE handles that signal before preparation by creating a fresh workspace under the same bound name and carrying a workspace-loss notice to the model. Other lookup failures and unhealthy live boxes still fail closed. Replacement permits a missing old box without attempting to rename or stop it; a concurrent creator is adopted under the same name.

The typed signal and absent-old-box replacement support belong upstream. Open SWE's pre-prepare recovery and user notification remain application policy. No automatic recovery can restore files from the deleted workspace.
