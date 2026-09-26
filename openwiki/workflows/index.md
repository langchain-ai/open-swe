# Files

- [Context Construction](context-engineering.md) - How Open SWE converts inbound surface events into attributed structured transcripts, then assembles durable provenance, run-specific prompt material, repository guidance, and read-only skills for model calls.
- [Follow-ups, Interruption, and Completion](follow-up-messages.md) - How later input preempts, waits behind, or enters a running thread; how deferred messages are recovered; and how completion webhooks settle work and notify origin surfaces.
- [Invocation from Product and Webhook Surfaces](invocation.md) - How dashboard, GitHub, Slack, Linear, desktop, and scheduled inputs are admitted, attributed, routed to threads, and dispatched as durable LangGraph runs.
- [Code Delivery and Pull Request Creation](pr-creation.md) - Guarded delivery of sandbox changes from push through attributed pull-request creation, workflow-file approval, and CI or review handoff. Explains the thread records and failure behavior that connect GitHub delivery to follow-up work.
- [Pull Request Review Workflow](pr-review.md) - How Open SWE admits GitHub pull-request reviews, runs a diff-grounded reviewer, stores and reconciles findings, publishes GitHub reviews, and settles review checks across subsequent pushes.
- [Schedules, Background Tasks, and CI Watching](scheduling-and-baby-sit.md) - Model-free scheduler routing for recurring automations, delayed maintenance, sandbox background commands, thread wakeups, and durable pull-request CI watches.
