Record a suggested task for concrete future work without starting it, opening a ticket, or changing a repository.
Available in agent, reviewer, and review-scout threads.
Use this for pre-existing or out-of-scope bugs and improvements rather than expanding the current task.

Pass a short `summary`, actionable `details`, and the target `repo` as `owner/name`.
Pass `workspace` to select the target workspace; otherwise use the repository's preferred workspace, falling back to this thread's workspace.
The target may differ from this thread's workspace and repository, provided the triggering user has access to that repository.
Pass additional evidence URLs in `context_links`, such as issues, code locations, or traces.
The originating thread, task, pull request, and triggering user are linked automatically when available.

Suggestions are saved durably in PostgreSQL and publish a `workspace` event of type `task.suggested`.
Do not include secrets or credentials.
