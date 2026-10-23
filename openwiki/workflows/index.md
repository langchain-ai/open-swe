# Files

- [Context engineering and run preparation](context-and-agent-preparation.md) - How Open SWE normalizes source input, assembles prompt and workspace context, and prepares a checkpointed agent run while keeping untrusted material and private context within explicit visibility boundaries.
- [Implementation, approvals, and pull-request delivery](implementation-and-pr-delivery.md) - How an Open SWE coding thread obtains and preserves a sandbox, gates sensitive pushes, opens attributed GitHub pull requests, and follows CI through durable baby-sit watches.
- [Inbound invocation and follow-up delivery](invocation-and-follow-ups.md) - How dashboard, GitHub, Slack, Linear, schedules, and clients select threads and workspaces, normalize input, create durable runs, and deliver follow-ups and completion outcomes.
- [Pull-request review and findings workflow](pull-request-review.md) - How Open SWE admits pull-request reviews, prepares and runs the reviewer, stores diff-scoped findings, publishes GitHub reviews, and reconciles later commits and human thread activity.
- [Schedules, watches, and background recovery](scheduled-and-background-automation.md) - How Open SWE persists and executes timed automation, CI watches, thread wakeups, workspace refreshes, and sandbox completion handoff, including claims, retries, and recovery behavior.
