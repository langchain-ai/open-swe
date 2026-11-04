List a page of public and private Slack channels the Open SWE bot belongs to, excluding archived channels.
Returns channel IDs, names, privacy, and `next_cursor`.
Pass a nonempty `next_cursor` as `cursor` to continue, even when the current page has no channels.
Stop when `next_cursor` is empty.

Use names and IDs already available in channel context or previous tool results first; do not re-list just to confirm a known display name.
Use this to find an unknown destination channel for a requested Slack post.
Results populate the persistent channel directory.
Match the requested channel name exactly; do not guess channel IDs.
Show people `#channel-name`, unless the user explicitly asks for the raw ID.
Channel membership does not guarantee posting permission: Slack can restrict individual channels.
