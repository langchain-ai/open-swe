# Files

- [Context Engineering and Prompt Preparation](context-engineering.md) - How Open SWE turns surface events and durable provenance into structured graph input, then prepares main-agent, reviewer, and analyzer prompts with scoped instructions, settings, plans, and skills.
- [Follow-Up, Interrupt, and Queue Handling](follow-up-messages.md) - How Open SWE authorizes and routes follow-ups to durable threads, chooses interruption or run enqueueing, injects pending messages and autofix events before model calls, and responds to stop and completion events.
- [Invoking Work Across Product Surfaces](invocation.md) - How dashboard, desktop, GitHub, Slack, Linear, and schedule requests are admitted, authorized, routed to durable LangGraph runs, and handled when they finish.
- [Pull Request Creation and Delivery Controls](pr-creation.md) - How Open SWE pushes agent work, creates or updates attributed GitHub pull requests, records delivery state, and gates workflow-file pushes. Covers CI and feedback linkage, dashboard visibility, lifecycle resolution, and Slack review handoff.
- [Pull Request Review and Re-review](pr-review.md) - How Open SWE admits and dispatches GitHub pull-request reviews, keeps durable reviewer and finding state, publishes anchored findings, and re-reviews changes and feedback.
- [Scheduled Work, Background Tasks, and CI Monitoring](scheduling-and-baby-sit.md) - Model-free scheduler routing for recurring work, delayed maintenance jobs, thread wakeups, sandbox task monitoring, and durable pull-request CI watches.
