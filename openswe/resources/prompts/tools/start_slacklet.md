Start a slacklet in this Slack thread: one live message Open SWE keeps up to date.

A slacklet's state changes only through typed commands. Each one is stored permanently and queued
for you as a note listing the command and the state it left, so you learn what changed without
reading the message. Starting the same kind and subject again in this thread returns the existing
slacklet.

Kinds:
- `pull_request`: `subject` is a GitHub pull request URL. The message tracks its title,
  draft/open/merged/closed status, head commit, CI and reviews, with the commands `attach`,
  `set_title`, `set_status`, `set_head`, `set_ci` and `set_review`.

Use it when someone asks to follow, track or watch something in this thread that a kind covers.
The slacklet is itself the reply to that request: finish with `slack_no_reply_needed` unless there
is something else to say.
