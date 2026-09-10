Post all current findings to the PR as a GitHub Review.

Call this once at the end of a review run, after you have finished adding
findings (and, on a re-review, after marking resolved findings via
``update_finding``). The tool posts one GitHub PR Review for eligible
inline findings, records the GitHub comment/thread IDs for future
re-reviews, resolves GitHub threads for findings now marked resolved, and
advances the reviewer thread's ``last_reviewed_sha``.

On a re-review with no new findings to surface, it skips posting a new
GitHub Review but still resolves fixed threads and updates reviewer state.

Args:
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
