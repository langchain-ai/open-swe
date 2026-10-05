Update a workspace automation, preserving omitted fields.

Use ``clear_slack_channel`` to remove the Slack destination.

``triggers`` replaces every trigger, in the same shape ``create_automation``
takes; pass the full list, including triggers you are keeping. An automation
keeps at least one trigger.

Pass ``workspace`` (a workspace slug) to move the automation: later runs launch
in that workspace.
