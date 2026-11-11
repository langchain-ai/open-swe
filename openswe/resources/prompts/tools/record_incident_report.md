Record this turn's incident report.
Every claim needs evidence_ids from the incident context blocks or tool results.
Fill problem, previous_occurrence, impact, cause, and next_steps: they are published as named sections.
Stores the report, updates the postmortem summary, and posts to the channel when a responder asked a question, or once for the first automatic investigation that reaches a supported conclusion.
Successfully record a report at the end of the turn, including on turns that will not post.
Empty reports and reports with no surviving cited claims are rejected without posting; repair the content or evidence IDs and retry.
If no observation is supported, describe concrete gaps or open questions instead.
