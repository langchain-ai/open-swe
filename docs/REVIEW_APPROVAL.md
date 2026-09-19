# Review assessments and optional approval

Without a configured approval policy, Open SWE publishes ordinary reviews without
an approval assessment. Configuring a policy adds a 1–5 risk score and **Would
approve** or **Needs human review**. The explanation is collapsed under **Why?**,
alongside the reviewed commit. Configuring a policy alone is advisory.

Admins can edit **Approval policy** alongside the existing reviewer settings,
independently of review guidelines. It follows the existing instance/workspace
settings inheritance. In **Review Style Prompts**, a repository's approval policy
replaces the shared policy; clearing it restores inheritance. Analysis of review
style never changes this policy. There is no default policy. Clearing the instance
policy disables assessments wherever no workspace or repository policy applies.
Outstanding findings always require human review, even below the inline severity
threshold. A score is a model judgment, not a calibrated probability.

**Submit GitHub approvals** is a separate instance/workspace reviewer setting,
off by default. When enabled, a would-approve assessment may submit an actual
GitHub approval. The host checks that a policy is still configured and unchanged
from the review, no unresolved findings remain, and GitHub still reports the
reviewed head on an open, non-draft PR. Approvals are submitted for that exact
commit. The toggle has no effect without an applicable policy. No PR is merged.

React 👍 or 👎 on GitHub, or use **Rate assessment** in the review UI to leave a
helpful/not-helpful rating and optional comment. Open SWE saves UI feedback per
person and published assessment. The form collapses after saving and can be
reopened to edit. UI comments are not posted to GitHub. Feedback does not
automatically change the policy.

A completed re-review posts a fresh assessment for its reviewed commit. If a
push changes that commit before publication, the reviewer must reassess it.
Eval runs do not post assessments, and partial publication retries omit them.
