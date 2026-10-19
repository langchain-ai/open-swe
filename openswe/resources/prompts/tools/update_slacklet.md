Apply one command to a slacklet you created with `create_slacklet` in this Slack thread.

Your program's `apply(state, {op, params})` decides the new state, then `render` redraws the message.
If `apply` throws or `render` returns something invalid, nothing is applied and you get the error.
Otherwise the command is stored permanently with the next sequence number and appears in the web
UI's command log; `note` (optional, up to 200 characters) is how that log describes it to people.

Slacklets of built-in kinds such as `pull_request` change on their own and refuse commands here.
