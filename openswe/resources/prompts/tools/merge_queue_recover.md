Propose one recovery action after reading fresh merge_queue_recovery_status.
Provide exact batch/attempt/candidate identity and a new UUID request_key.
Reuse the identical proposal and key if its response is lost.
The controller validates current authority and budgets.
Actions: retry the full candidate workflow; defer exact implicated PR entries/heads (member plus additional_members); pause for an evidenced shared outage; or escalate a concrete need for human action.
Keep reason to a short causal issue, evidence to a short observed failure, and suggested_fix to a concrete fix.
For multiple deferrals, attribute each issue and fix to its PR; all removals must be confirmed before survivor validation.
The notifier supplies PR and run links; omit repeated URLs, batch IDs, SHAs, and investigation narration from these fields.
For defer/escalate, suggested_fix must describe a concrete next step.
A deferral proposal is pending until GitHub confirms removal; do not announce it before merge_queue_notify lists its confirmed outcome.
Escalation alone does not globally pause.
Never use except_tests or approve a candidate with a failing workflow, even when allow_test_exceptions is enabled or the failure is solely a test defect or flake.
Retry justified flakes; defer implicated batch PRs or report pre-existing test defects for correction.
Validation must pass before landing.
No action force-merges or extends deadlines.
