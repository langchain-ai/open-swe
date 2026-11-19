Update an automation you created, or any automation as an admin, preserving omitted fields.
Pass ``enabled: false`` to pause it.
Only admins edit system automations, and pass ``system`` to make one personal or system.
A personal automation runs as its creator; there is no personal automation cap.

``triggers`` replaces every trigger, in the same shape ``create_automation`` takes; pass the full list, including triggers you are keeping.
An automation keeps at least one trigger.
Schedule triggers accept an IANA ``timezone`` such as ``America/New_York``, defaulting to UTC when omitted.
Preserve each existing trigger’s time zone when replacing triggers unless the user requests a change; use a region time zone for schedules that follow local daylight saving time.
