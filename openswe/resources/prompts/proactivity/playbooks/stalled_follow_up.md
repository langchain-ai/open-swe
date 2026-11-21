---
name: Stalled thread check
evidence: stalled_threads
signal: Threads the user cares about have open asks and have gone quiet for days.
offer: A weekly check of their unresolved threads that have gone quiet, with a suggested next step for each.
never: Posts in those threads or messages anyone but the user.
cron: "0 10 * * 1"
---
List my unresolved Robin threads with no activity in the last three days.
For each, say what was left open and suggest one next step, with a link to the thread.
Send it to me as a DM and nowhere else.
If none have gone quiet, send nothing.
