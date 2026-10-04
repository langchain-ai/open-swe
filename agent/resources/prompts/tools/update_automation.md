Update a workspace automation, preserving omitted fields.

Use ``clear_repo`` or ``clear_slack_channel`` to remove those destinations.

Triggers are grouped by provider, and each argument replaces only its own:
``schedule`` sets the cron and ``clear_schedule`` removes it; ``github_events``
sets the GitHub events ("issues.opened", "pull_request.opened",
"pull_request.closed", "pull_request.merged"), and an empty list removes them.
GitHub events need a repo, and an automation keeps at least one trigger.

Pass ``workspace`` (a workspace slug) to move the automation: later runs launch
in that workspace.
