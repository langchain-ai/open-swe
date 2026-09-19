# Files

- [Run Context and Prompt Construction](context-engineering.md) - How inbound events become attributed model input and how source guidance, repository and workspace instructions, sender metadata, skills, and plan state are assembled for an agent run.
- [Follow-ups, Interrupts, and Completion](follow-up-messages.md) - How surface replies continue a thread, interrupt or enqueue durable work, inject messages into a live run, and handle stop and completion updates.
- [Inbound Invocation and Durable Dispatch](invocation.md) - How signed integration events and scheduled automation become authorized, structured, thread-bound LangGraph runs with durable streaming and completion handling.
- [Code Delivery and Pull Request Creation](pr-creation.md) - How an agent delivers sandbox changes through guarded pushes and attributed GitHub pull requests, then tracks status, feedback, approvals, and lifecycle updates.
- [Pull Request Review and Re-review](pr-review.md) - How Open SWE starts GitHub pull-request reviews, prepares a diff-grounded reviewer run, persists and publishes findings, and reconciles replies, resolutions, and review checks across later pushes.
- [Scheduled Work, CI Monitoring, and Background Automation](scheduling-and-baby-sit.md) - Model-free scheduler routing for recurring automations, delayed maintenance jobs, workspace refreshes, and background task completion. Covers the durable, opt-in baby-sit workflow that monitors pull-request CI with signed webhooks and bounded fallback polling.
