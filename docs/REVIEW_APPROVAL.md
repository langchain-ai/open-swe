# Advisory review assessments

Open SWE includes a 1–5 risk score and **Would approve** or **Needs human review**
in its GitHub review. The explanation is collapsed under **Why?**, alongside the
reviewed commit. This is advisory: it never submits an approval or merges a PR.

Admins can edit **Approval policy** alongside the existing reviewer settings,
independently of review guidelines. It follows the existing instance/workspace
settings inheritance. In **Review Style Prompts**, a repository's approval policy
replaces the shared policy; clearing it restores inheritance. Analysis of review
style never changes this policy. The built-in
[default policy](../agent/resources/prompts/reviewer/approval-policy.md) applies
when no custom policy is set.
Outstanding findings always require human review, even below the inline severity
threshold. A score is a model judgment, not a calibrated probability.

React 👍 to agree with the assessment or 👎 to disagree, and comment on the PR
with context. GitHub stores the reactions on that review; Open SWE does not copy
them into a separate feedback store or automatically learn policy changes from
them. There is no additional settings page or feedback form.

A completed re-review posts a fresh assessment for its reviewed commit. If a
push changes that commit before publication, the reviewer must reassess it.
Eval runs do not post assessments, and partial publication retries omit them.
