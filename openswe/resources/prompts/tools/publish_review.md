Post all current findings to the PR as a GitHub Review.

Call this once at the end of a review run, after you have finished adding findings (and, on a re-review, after marking resolved findings via ``update_finding``).
The tool posts one GitHub PR Review for eligible inline findings, records the GitHub comment/thread IDs for future re-reviews, resolves GitHub threads for findings now marked resolved, and advances the reviewer thread's ``last_reviewed_sha``.

On a re-review with no new findings or assessment, it skips posting a new GitHub Review but still resolves fixed threads and updates reviewer state.

Args:
- ranking: Required.
  Every open finding this review would newly post, as finding IDs ordered from most to least important, each exactly once.
  Order by your own judgment of what the author most needs to fix first; severity is an input, not the rule.
  Pass ``[]`` when there are none.
  A ranking with missing, unknown, or repeated IDs publishes nothing and returns ``expected_finding_ids`` so you can rank that exact set.
- description_review: Required only when the reviewer instructions enable the PR author’s description-verification experiment; omitted otherwise and in eval mode.
  Your verified description verdict: exact full `head_sha`, current `title`, current `body` (empty string if absent), and `issues` (a list of evidence-backed material inaccuracies or omissions, empty when clean).
  Optional `verbosity_suggestions` lists up to five actionable ways to remove repetition, boilerplate or unnecessary narration without losing useful context.
  These render as advisory suggestions only; they never request changes, prevent dismissal of the description block, or change the approval assessment.
  Never put verbosity-only concerns in `issues`.
  The tool rechecks the live PR text and head before publication and rejects stale verdicts.
  Nonempty issues submit a real `REQUEST_CHANGES` review even with no inline findings or approval policy, and prevent automatic approval.
  Startup already placed a description change request; failures or incomplete verification leave it standing.
  An empty issues list dismisses only this reviewer's marked description change requests, never human or unrelated reviews, without approving the PR.
  Description verdicts publish on re-reviews even with no new inline findings.
  Eval mode never submits change requests.
- assessment: Optional structured advisory approval assessment with the full ``head_sha`` inspected, integer ``risk_score`` (1 = low, 5 = high), ``decision`` (``would_approve`` or ``needs_human_review``), and a short ``explanation`` (up to 1500 characters).
  Include it after completing a review only when the reviewer instructions include an approval policy, and assess against that policy.
  The commit must match the live reviewed head.
  Unresolved findings force ``needs_human_review``.
  A new assessment is published even when a re-review has no new findings.
  GitHub keeps native thumbs-up/down feedback on the review.
  The host may submit an approval only when an admin has set the repository to approve and the reviewed commit is still current; otherwise it posts the assessment as a comment.
  No merge is performed.
  Assessments are omitted in eval mode and when publication retries with only a subset of findings.
- severity_threshold: Lowest severity to surface as inline GitHub comments (default ``medium``).
  Lower-severity findings stay in state and are mentioned in the review summary with a link to the web app, but are not posted as inline PR comments.
  Returns: Dictionary with ``success``, ``review_id``, ``surfaced_count``, ``hidden_count``, ``resolved_thread_count``, and sometimes ``unresolvable_findings``, plus the flags below.

  ``success: true`` alone does NOT mean a GitHub Review was posted — check the flags:

    - ``skipped_empty_re_review: true`` (with ``review_id: null``): an empty re-review was deliberately skipped.
      No GitHub Review was created; the call was a valid no-op.
      Do not describe the review as published/posted/submitted.
    - ``dry_run: true`` (with ``review_id: null``): eval/benchmark mode — the publish was simulated and nothing was posted to GitHub.
      Do not claim publication.

    Only a numeric ``review_id`` (with neither flag set) confirms a real GitHub Review was created.
