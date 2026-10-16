Follow the current Slack channel as an incident, or turn incident analysis off and on.

`start` makes Incidents follow this public channel: it posts an introduction in
the channel, reads recent history as context, and investigates new messages and
alerts as they arrive. Use it only when a person explicitly asks for this channel
to be monitored, followed, or investigated as an incident; on a paused or
completed incident it resumes. `pause` stops automatic analysis without closing
the incident, `resume` turns it back on (and reopens a completed incident), and
`complete` closes it and posts the final summary. The tool notifies the channel
itself, so do not post a separate status message. It acts only on the channel
this conversation is in.
