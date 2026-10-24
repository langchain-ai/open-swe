# Files

- [Schedules, Jobs, Monitoring, and Merge Queue Automation](background-automation.md) - Durable Postgres jobs drive scheduled work and monitors, while event matches safely wake agent threads. This page explains leasing, retries, CI and sandbox reconciliation, and the separately configured merge-queue controller and validation contract.
- [Coding, Sandbox, and Pull Request Delivery Workflow](coding-and-delivery.md) - How a prepared Open SWE thread obtains a cloud or desktop-backed sandbox, performs guarded repository work, and delivers changes through attributed pull requests, approvals, CI, and follow-up runs.
- [Invocation, Thread Creation, and Follow-up Routing](invocation-and-thread-routing.md) - How dashboard, Slack, GitHub, Linear, API, and scheduled work become normalized durable runs, are routed through the PostgreSQL thread index to Managed Deep Agents, and are settled or continued.
- [Pull Request Review and Human Decision Workflows](pull-request-review.md) - How Open SWE prepares and publishes automated PR reviews, supports human reviewers in Slack, manages expedited and standard approvals, and performs guarded merges.
