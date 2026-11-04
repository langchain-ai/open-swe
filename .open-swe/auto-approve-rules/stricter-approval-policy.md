---
match-all-files:
  - .open-swe/auto-approve-rules/*
---
Open SWE may approve a pull request that has no unresolved findings and changes only the rules in `.open-swe/auto-approve-rules/`, solely to narrow the set of pull requests eligible for automatic approval (for example, adding an exclusion or tightening a limit).
Any project member may propose restrictions on what they would approve.

Compare the whole diff against the base rules: every pull request eligible under the proposed rules must already be eligible under the base rules, and at least some previously eligible pull requests must become ineligible.
If that cannot be established, require human review.
Mixed changes, relaxed rules, new exceptions, new or widened `match-all-files` or `match-any-files` scopes on an approving rule, any narrowed scope on a rule that requires human review, and changes to authorization or human approval requirements do not qualify.

This rule waives only the instruction-file exclusion for `.open-swe/auto-approve-rules/`; all other human-review requirements still apply.
Evaluate the pull request using the base rules, not the rules it proposes.
