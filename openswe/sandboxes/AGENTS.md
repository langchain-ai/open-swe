# AGENTS.md

Managed Deep Agents creates and replaces every thread's sandbox; the backend only connects to one by its id, and must never create one.
An unreachable existing sandbox must raise rather than be abandoned, because a fresh one would lose the thread's work.
Only the read-only reviewer and review scout may opt into abandoning one, which hands the thread to MDA for its next run.

The deployment's sandboxes are LangSmith's (`providers/langsmith.py`).
