# Files

- [Context and Prompt Engineering](context-engineering.md) - Workflow for assembling run input, managing dynamic context deduplication, and constructing layered system prompts from multiple instruction sources.
- [Follow-up Messages and Background Work](follow-up-messages.md) - Store-backed message queues for live handoffs, background task monitoring, run completion webhooks, and emergency stop mechanics. Explains how queued messages inject into active runs and how terminal conditions dispatch follow-ups.
- [Inbound Invocation to Durable Run](invocation.md) - How GitHub, Slack, Linear, dashboard, desktop, and scheduled automation inputs are admitted, attributed, routed to a thread, dispatched as durable LangGraph runs, and handled at completion.
- [Pull Request Creation and Workflow Approval](pr-creation.md) - How the agent creates and manages pull requests with attributed authorship, commits squashing, workflow-file change approval, and metadata recording.
- [Pull Request Review Workflow](pr-review.md) - How Open SWE starts GitHub pull-request reviews, prepares a diff-grounded reviewer run, persists and publishes findings, and reconciles replies, resolutions, and review checks across later pushes.
- [Scheduling, Background Work, and CI Monitoring](scheduling-and-baby-sit.md) - How the model-free scheduler routes cron and delayed work into recurring automations, reconciliation, cost refreshes, background-task monitoring, and opt-in pull-request CI recovery.
