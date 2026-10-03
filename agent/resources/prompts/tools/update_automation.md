Update a workspace automation, preserving omitted fields.

Use ``clear_repo`` or ``clear_slack_channel`` to remove those destinations.

Pass ``trigger`` to change how the automation fires. Switching to a GitHub
trigger requires the automation to have a repo and removes its schedule;
switching to "schedule" requires a ``schedule``.

Pass ``workspace`` (a workspace slug) to move the automation: later runs launch
in that workspace.
