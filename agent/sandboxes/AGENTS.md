# AGENTS.md

An unreachable existing sandbox must raise rather than be replaced, because replacement would lose the thread's work. Only the read-only reviewer may opt into replacement.

`coding_agent/sandboxes/lifecycle.py` owns the get-or-create flow; this package wires it to Open SWE — `lifecycle.py` supplies the snapshot resolver and the singleton `OPEN_SWE_SANDBOXES`, and `credentials.py` installs the GitHub App token on the sandbox proxy.

Providers go under `coding_agent/sandboxes/providers/` and are registered in `coding_agent/sandboxes/providers/registry.py`.
