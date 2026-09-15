# Files

- [Context Engineering and Plans](context-engineering.md) - How Open SWE turns surface events into attributed model context, assembles run-specific prompts and scoped instructions, and constrains planning through reviewed plan artifacts and approval-gated implementation.
- [Follow-up, Interruption, and Completion Handling](follow-up-messages.md) - How follow-up work is authorized and routed through durable runs or a live thread queue, and how stops, completion callbacks, feedback, telemetry, and stale-run recovery close the lifecycle.
- [Invocation from Dashboard, Webhooks, and Desktop](invocation.md) - How dashboard, GitHub, Slack, Linear, schedules, and desktop inputs are admitted, attributed, routed to threads, and dispatched as durable LangGraph runs.
- [Pull Request Delivery and Approval](pr-creation.md) - How an agent delivers code through attributed GitHub pull requests, protects workflow-file pushes with human approval, and records PR health and lifecycle state for the dashboard.
- [Pull Request Review and Re-review](pr-review.md) - GitHub and Slack entrypoints converge on a durable reviewer thread that prepares a diff-grounded sandbox run, publishes review findings, handles feedback and watched pushes, and settles GitHub checks.
- [Schedules, Background Tasks, and CI Monitoring](scheduling-and-baby-sit.md) - How the deterministic scheduler dispatches persistent automations and delayed maintenance work, including PR CI watches, environment refreshes, task completion, feedback, and cost enrichment.
