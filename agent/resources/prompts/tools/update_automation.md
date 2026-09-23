Update a workspace automation, preserving omitted fields.

Use ``clear_repo`` or ``clear_slack_channel`` to remove those destinations.

Pass ``trigger`` to change how the automation fires. Switching to
"github_issue_opened" requires the automation to have a repo and clears its
cron expression; switching to "schedule" requires a ``schedule``. Switching to
"slack_channel_message" requires a ``slack_channel_id`` and a
``message_pattern``; check a new pattern with ``preview_automation_matches``
before saving it.
