# AGENTS.md

Managed Deep Agents creates and replaces every thread's sandbox; the backend only connects to one by its id, and must never create one.
Every run works in an MDA sandbox: the backend never plans a run without one, and a sandbox it made before MDA owned them is handed to MDA as the next run is planned (`BackendSandbox` in `lifecycle.py`).
An unreachable existing sandbox must raise rather than be abandoned, because a fresh one would lose the thread's work.
Only the read-only reviewer and review scout may opt into abandoning one, which hands the thread to MDA for its next run.

The deployment's sandboxes are LangSmith's (`providers/langsmith.py`).
