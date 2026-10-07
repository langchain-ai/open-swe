# AGENTS.md

An unreachable existing sandbox must raise rather than be replaced, because replacement would lose the thread's work. Only the read-only reviewer may opt into replacement.

Providers go under `providers/` and are registered in `providers/registry.py`.
