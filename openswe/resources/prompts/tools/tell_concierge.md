Report to this person's concierge, the agent in their Slack DM with Open SWE. It is the only way anything you find reaches the person.

Write the report for the concierge: what you found or did, with links, and what the person needs to decide or do. The concierge tells the person in its own words.

Args:
    message: The report.
    wake: True (the default) when the person should hear it now; the concierge is started to tell them. False queues it for the concierge's next conversation with them.

Returns ``ok`` and ``woke``.
