Update an automation you created, or any automation as an admin, preserving omitted fields.
Pass ``enabled: false`` to pause it.
Only admins edit system automations, and pass ``system`` to make one personal or system; made personal, it counts toward its creator's three.

``triggers`` replaces every trigger, in the same shape ``create_automation`` takes; pass the full list, including triggers you are keeping.
An automation keeps at least one trigger.

Pass ``workspace`` (a workspace slug) to move the automation: later runs launch in that workspace.
