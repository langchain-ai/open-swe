Listen in every Slack channel whose name starts with a prefix, such as `inc-` for incident channels.
Requires an authorized admin; supported writes also work in verified sole-writer shared threads, returning only an acknowledgement.

- `prefix`: the channel name prefix, without `#`; lowercase letters, digits, `-` and `_`
- `prompt`: listening instructions for every matching channel, as for `bind_slack_channel`; use an empty string for standard behavior
- `kickoff`: optional instructions for a run Robin starts in each new matching channel it joins, such as investigating an incident; leave empty to only listen

Every public, internal, unarchived channel named with the prefix is a listening channel, and Robin joins channels created or renamed to match.
A channel's own binding takes precedence over a rule; the longest matching prefix wins between rules.
Updating a rule replaces its instructions and kickoff.
Instructions do not grant credentials, repository access, or tool permissions; a kickoff run acts for no one and only researches and replies in its own thread.
