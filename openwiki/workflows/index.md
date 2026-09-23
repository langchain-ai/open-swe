# Files

- [Context Assembly and Prompt Engineering](context-engineering.md) - How inbound events become durable, attributed transcripts and how run preparation adds source, workspace, repository, user, skill, reviewer, and recent-thread context to model-visible instructions.
- [Follow-ups, Interrupts, and Completion](follow-up-messages.md) - How later messages attach to a thread, either interrupt or wait behind active work, enter and drain the in-flight message queue, and receive stop and terminal-completion handling.
- [Inbound Invocation and Durable Dispatch](invocation.md) - How dashboard, GitHub, Slack, Linear, schedules, and automation establish durable thread context and invoke LangGraph runs through the shared dispatch contract. Covers admission, routing, stream recovery, concurrent follow-ups, and completion handling.
- [Pull Request Delivery and Approval](pr-creation.md) - How an agent safely delivers branch changes through attributed GitHub pull requests, guarded workflow pushes, human approval, and delivery-status feedback.
- [Pull Request Review Lifecycle](pr-review.md) - How Open SWE admits automatic and on-demand GitHub pull-request reviews, prepares a diff-grounded reviewer run, stores and publishes findings, and follows later pushes and human feedback.
- [Scheduling, Background Work, and CI Watching](scheduling-and-baby-sit.md) - How the model-free scheduler routes cron and delayed work, and how recurring agent schedules, workspace refreshes, background-task monitors, wakeups, feedback, cost enrichment, and baby-sit CI watches control their lifecycles.
