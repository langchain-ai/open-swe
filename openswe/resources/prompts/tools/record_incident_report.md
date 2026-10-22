Persist this turn's real incident report, update the postmortem summary, and post to the
channel when a responder asked a question, or once for the first automatic investigation
that reaches a supported conclusion. Call once at the end of the turn, including turns
that will not post; never call with placeholders to discover the schema.
summary, problem, previous_occurrence, impact, cause, and next_steps are lists of claims:
{"text": "API errors rose across the gateway", "evidence_ids": ["<known evidence ID>"]}. Use IDs from incident
context blocks or tool results. Fill the sections the investigation covered. hypotheses
contains {"title": "...", "assessment": "plausible", "evidence_ids": [...]} (assessment may
also be supported or rejected); gaps and questions are lists of strings. Omit unused fields.
Every summary/problem claim must be at least 20 characters after stripping whitespace.
Empty, placeholder-only, gaps-only, and questions-only drafts are rejected without writes;
claims with missing or unknown citations do not count. Inconclusive investigations still need
substantive evidence-backed claims or hypotheses. Keep the full current report: strict subsets
and regressions from findings to inconclusive are rejected before any write or channel post.
Identical retries, additions, and substantive corrections remain allowed.
