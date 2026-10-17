Attach a live pull request slacklet to this Slack thread.

The slacklet is one message that Open SWE keeps in step with the pull request: its title,
draft/open/merged/closed status, head commit, CI and reviews. Each change is applied as a
typed command (`attach`, `set_title`, `set_status`, `set_head`, `set_ci`, `set_review`),
posted once as an immutable reply in the thread, and queued for you as a note, so you learn
what changed without reading the message. Attaching the same pull request again in the same
thread returns the existing slacklet.

Use it when someone asks to follow, track, or watch a pull request in this thread. The
slacklet is itself the reply to that request: finish with `slack_no_reply_needed` unless
there is something else to say.
