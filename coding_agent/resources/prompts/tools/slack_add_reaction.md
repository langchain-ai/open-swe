Commit to acting on a Slack message by adding a context-appropriate reaction.

Use this only when work will continue and always follow up with the outcome; never react to
a message you are going to stay silent on. Prefer `saluting_face` for taking ownership,
`thinking_face` for investigation, and `tada` for genuine wins. Never use
`white_check_mark`, because teams use it to indicate that a pull request is approved.
To target a specific message, pass its `message_ts` identifier shown in Slack context.
If `message_ts` is omitted, this reacts to the latest message that triggered the run.
Pass emoji names without surrounding colons.
