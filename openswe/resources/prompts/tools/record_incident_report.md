Record this turn's incident report. Every claim needs evidence_ids from the incident
context blocks or tool results. Fill problem, previous_occurrence, impact, cause, and
next_steps: they are published as named sections. Stores the report, updates the
postmortem summary, and posts to the channel when a responder asked a question, or once
for the first automatic investigation that reaches a supported conclusion. Call it exactly
once at the end of the turn, including on turns that will not post.
