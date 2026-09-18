Update a workspace automation, preserving omitted fields.

Every run gets a new thread. By default, those threads share one sandbox, so files written in the sandbox persist between runs. Set ``sandbox_mode`` to ``new`` for a fresh sandbox on every run.

Use ``clear_repo`` or ``clear_slack_channel`` to remove those destinations.

Pass ``trigger`` to change how the automation fires. Switching to
"github_issue_opened" requires the automation to have a repo and clears its
cron expression; switching to "schedule" requires a ``schedule``.
