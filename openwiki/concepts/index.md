# Files

- [Models, Prompts, Skills, and Settings Resolution](models-prompts-and-settings.md) - How Open SWE resolves workspace, user, thread, and run configuration into a signed MDA run, and how that run assembles models, prompt layers, and skills at execution time.
- [Run State, Messages, Transcript, and Delivery Guarantees](run-state-and-transcript.md) - How Open SWE separates MDA checkpoints from PostgreSQL records, signed run settings, thread metadata, structured messages, and the transcript. Covers inbox and outbox acknowledgement, idempotency, turn lifecycle, and completion recovery.
- [Authentication, Authorization, and Credential Boundaries](security-and-authorization.md) - How Open SWE authenticates dashboard, Agent Server, MDA channel, and remote-runtime requests, and how it limits credentials across backend services, user connections, GitHub access, and sandboxes.
- [Tool Catalog, Operations, and Sandbox Access](tools-and-sandbox-access.md) - How an Open SWE MDA run assembles and dispatches its tool surface, routes file and shell work, and owns a thread sandbox, browser, and credential boundaries.
