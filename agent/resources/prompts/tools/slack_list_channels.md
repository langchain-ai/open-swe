List a page of public and private Slack channels the Open SWE bot belongs to,
excluding archived channels. Returns channel IDs, names, privacy, and
`next_cursor`. Pass a nonempty `next_cursor` as `cursor` to continue, even when
the current page has no channels. Stop when `next_cursor` is empty.

Use this to find the destination channel for a requested Slack post. Match the
requested channel name exactly; do not guess channel IDs. Channel membership
does not guarantee posting permission: Slack can restrict individual channels.
