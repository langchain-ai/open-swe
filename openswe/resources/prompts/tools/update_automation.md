Update a workspace automation, preserving omitted fields.

``triggers`` replaces every trigger, in the same shape ``create_automation``
takes; pass the full list, including triggers you are keeping. An automation
keeps at least one trigger. GitHub triggers support ``workflow_run.completed``;
optional ``conclusion`` (for example, ``"failure"``) filters workflow completion
events only. Omit it to match all conclusions.

Pass ``workspace`` (a workspace slug) to move the automation: later runs launch
in that workspace.
