# Files

- [Scheduling, jobs, tasks, and monitoring](background-automation.md) - PostgreSQL-backed background execution for recurring automation, delayed thread wakeups, sandbox completion reporting, CI watches, worker tasks, and stale-run recovery. Explains leases, retries, deduplication, and the rule that user-visible work returns through its owning thread.
- [Pull request work, review, and delivery](review-and-delivery.md) - How Open SWE creates and changes pull requests under guarded write paths, then coordinates automated review, human or expedited approval, CI follow-up, and GitHub and Slack feedback.
- [Invocation, dispatch, and continuation](run-lifecycle.md) - How dashboard, API, GitHub, Slack, and Linear work become durable agent runs, and how threads, inboxes, runtime reports, completion, and follow-up pickup preserve the conversation.
