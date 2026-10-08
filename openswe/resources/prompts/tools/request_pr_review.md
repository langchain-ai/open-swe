Start the automated reviewer agent for a GitHub pull request URL. Use when the user says "please review this PR" or "review this PR", explicitly requests an automated/AI review, or asks to start/run the reviewer agent. Requests to "get this PR reviewed", "get it reviewed", "request a review", or explicitly obtain human review mean human review: use `request_human_review`, or `expedite_pr_approval` for a qualifying tiny change, instead.

Set `use_mda` only when the user asks for the review to run on Managed Deep Agents (MDA).
