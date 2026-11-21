Set up the automation for a proactive offer the requester just accepted, in their own private dashboard thread.

- `playbook`: the offer's id, as the proactivity context lists it.
- `details`: what this automation covers for them, in their words: the channels, repos, topic or commitment.
  Leave it empty when the offer needs none.
- `cron`: a five-field cron schedule when they asked for a different time; omit it for the playbook's default.
- `timezone`: their IANA time zone when you know it.

The automation runs as them, in private threads they own, and delivers by DM.
Tell them its name and when it first runs.
