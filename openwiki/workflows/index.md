# Files

- [Input Context and Prompt Construction](context-engineering.md) - How events from Slack, Linear, GitHub, and other surfaces become structured run input, then combine with source provenance, dynamic identities, instructions, repository conventions, and virtual skills for agent and analyzer prompts.
- [Follow-ups, Interrupts, and Stop Control](follow-up-messages.md) - How Open SWE attaches new work to an existing thread, chooses durable run interruption or enqueueing, preserves checkpoint and sandbox context, and implements Slack and dashboard stop behavior.
- [Inbound Invocation to Durable Run](invocation.md) - How GitHub, Slack, Linear, dashboard, desktop, and scheduled automation inputs are admitted, attributed, routed to a thread, dispatched as durable LangGraph runs, and handled at completion.
- [Pull Request Delivery and Approval](pr-creation.md) - How an agent delivers code through GitHub branches and pull requests, including attributed creation, workflow-change approval, status visibility, CI handling, and review handoff.
- [Pull Request Review Workflow](pr-review.md) - How Open SWE starts GitHub pull-request reviews, prepares a diff-grounded reviewer run, persists and publishes findings, and reconciles replies, resolutions, and review checks across later pushes.
- [Scheduling, Background Work, and CI Monitoring](scheduling-and-baby-sit.md) - How the model-free scheduler routes cron and delayed work into recurring automations, reconciliation, cost refreshes, background-task monitoring, and opt-in pull-request CI recovery.
