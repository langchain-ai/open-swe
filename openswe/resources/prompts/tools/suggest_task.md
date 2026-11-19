Record a suggested task for concrete future work without starting it, opening a ticket, or changing a repository.
Available in agent, reviewer, and review-scout threads.
Use this for pre-existing or out-of-scope bugs and improvements rather than expanding the current task.

Pass a short `summary`, actionable `details`, and the target `repo` as `owner/name`.
The target repository may differ from this thread's, provided the triggering user has access to it.
Pass additional evidence URLs in `context_links`, such as issues, code locations, or traces.
The originating thread, task, pull request, and triggering user are linked automatically when available.

Suggestions are saved durably in PostgreSQL and publish a `robin` event of type `task.suggested`.
Do not include secrets or credentials.
