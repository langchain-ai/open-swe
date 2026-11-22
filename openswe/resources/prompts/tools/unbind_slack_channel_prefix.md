Remove a Slack channel name rule, so Robin stops listening in channels that only the rule matched.
Requires an authorized admin; supported writes also work in verified sole-writer shared threads, returning only an acknowledgement.
Pass the rule's `prefix`.
Robin stays in the channels it joined and channels bound individually keep listening; this deletes no channel or thread.
