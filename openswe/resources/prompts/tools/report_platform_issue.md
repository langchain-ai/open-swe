Report an issue with the sandbox or execution environment.
Requires a non-empty problem description.
Reports are saved as admin-only `platform_issue` events and public reports are sent to the workspace's memory curator explicitly as problem reports, not memories.
Private reports and reports whose thread visibility cannot be verified are not sent to the public curator thread.
Check `event_log_status` for persistence, `curator_status` for forwarding, and `export_status` for feedback on this thread's LangSmith trace.
A failed destination does not prevent delivery to the others.
