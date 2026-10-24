Update a workspace automation, preserving omitted fields.

``triggers`` replaces every trigger, in the same shape ``create_automation``
takes; pass the full list, including triggers you are keeping. An automation
keeps at least one trigger. GitHub ``base_branches`` filters PR base refs with
case-sensitive fnmatch patterns; omit or null for all branches.
Filtered PRs without a base ref do not fire; issue events ignore this filter.

Pass ``workspace`` (a workspace slug) to move the automation: later runs launch
in that workspace.
