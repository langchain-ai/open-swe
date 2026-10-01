Post all current findings to the PR as a GitHub Review.

Call this once at the end of a review run, after all investigation, delegated
work, verification, and any approval assessment are complete. Submit findings
and the assessment together in this single call (on a re-review, first mark
resolved findings via ``update_finding``). The tool posts one GitHub PR Review
for eligible inline findings, records the GitHub comment/thread IDs for future
re-reviews, resolves GitHub threads for findings now marked resolved, and
advances the reviewer thread's ``last_reviewed_sha``.

On a re-review with no new findings or assessment, it skips posting a new
GitHub Review but still resolves fixed threads and updates reviewer state.

Args:
    ranking: Required. Every open finding this review would newly post, as
        finding IDs ordered from most to least important, each exactly once.
        Order by your own judgment of what the author most needs to fix first;
        severity is an input, not the rule. Pass ``[]`` when there are none.
        A ranking with missing, unknown, or repeated IDs publishes nothing and
        returns ``expected_finding_ids`` so you can rank that exact set.
    assessment: Optional structured advisory approval assessment with the full
        ``head_sha`` inspected, integer ``risk_score`` (1 = low, 5 = high),
        ``decision`` (``would_approve`` or ``needs_human_review``), and a short
        ``explanation`` (up to 1500 characters). Include it after completing a
        review only when the reviewer instructions include an approval policy,
        and assess against that policy. The commit must match the live
        reviewed head. Unresolved findings force ``needs_human_review``. A new
        assessment is published even when a re-review has no new findings,
        but another call on the same run and commit with no new findings is
        skipped. Do not republish just to update the assessment explanation.
        GitHub keeps native thumbs-up/down feedback on the review. The host may
        submit an approval only when an admin has set the repository to
        approve and the reviewed commit is still current; otherwise it posts
        the assessment as a comment. No merge is performed. Assessments are
        omitted in eval mode. Anchor-recovery retries retain the assessment
        alongside the findings as an advisory comment, never an approval.
    severity_threshold: Lowest severity to surface as inline GitHub comments
        (default ``medium``). Lower-severity findings stay in state and are
        mentioned in the review summary with a link to the web app, but are
        not posted as inline PR comments.
Returns:
    Dictionary with ``success``, ``review_id``, ``surfaced_count``,
    ``hidden_count``, ``resolved_thread_count``, and sometimes
    ``unresolvable_findings``, plus the flags below.

    ``success: true`` alone does NOT mean a GitHub Review was posted —
    check the flags:

    - ``skipped_empty_re_review: true`` (with ``review_id: null``): an
      empty re-review was deliberately skipped. No GitHub Review was
      created; the call was a valid no-op. Do not describe the review as
      published/posted/submitted.
    - ``dry_run: true`` (with ``review_id: null``): eval/benchmark mode —
      the publish was simulated and nothing was posted to GitHub. Do not
      claim publication.

    Only a numeric ``review_id`` (with neither flag set) confirms a real
    GitHub Review was created.
