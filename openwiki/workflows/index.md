# Files

- [Coordinator and Worker Task Collaboration](collaborative-tasks.md)
- [Context and Prompt Assembly](context-engineering.md) - How Open SWE turns inbound content and provenance into durable runs, then assembles prompt templates, instructions, participants, recent context, skills, and sandbox paths for model calls.
- [Follow-ups, Interrupts, and Completion Delivery](follow-up-messages.md) - How Open SWE accepts concurrent follow-ups, chooses durable interruption or enqueueing, routes replies to the active conversation surface, and recovers from cancellation or stale runs.
- [Inbound Invocation and Durable Dispatch](invocation.md) - How dashboard, Slack, GitHub, Linear, desktop, and schedule inputs are verified, routed, normalized, and created as durable LangGraph runs, including completion handling.
- [Implementation, Push, and Pull Request Creation](pr-creation.md) - Trace agent implementation from its sandbox branch through guarded push, attributed pull request creation, CI and feedback handling, and human review or merge handoffs.
- [Pull Request Review and Finding Publication](pr-review.md) - How Open SWE triggers and prepares pull-request reviews, waits for an optional review-scout walkthrough, stores findings, publishes GitHub reviews, and follows later pushes and human feedback.
- [Scheduling, Monitoring, and Background Work](scheduling-and-baby-sit.md) - How the model-free scheduler routes cron and delayed work, recovers stuck dispatches, refreshes costs and workspaces, delivers background-task completion, and monitors opted-in pull-request CI.
