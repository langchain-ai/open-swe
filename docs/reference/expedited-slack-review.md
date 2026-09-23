# Expedited Slack review for small pull requests

This document records the design and operating constraints of expedited Slack review. The feature is experimental and off by default.

## Summary

The agent posts a tiny PR's full diff in the Slack thread with Approve and Reject
buttons. Clicks only record who approved; they are stored in PostgreSQL and nothing
reaches GitHub. Once checks and reviews are clean, the agent calls a merge tool that
submits each non-author approval as that person's GitHub review and merges. The votes
survive a later commit only if it leaves the diff shown on the card unchanged.

## Motivation

For a 5-line change, opening GitHub takes longer than reviewing it. The reviewers
are already in the Slack thread.

## Design

### Enablement

- Experimental. Off by default; an admin turns it on per Open SWE instance in
  Settings. Turning it off hides both tools.
- Available only in threads that have a Slack location, or when the agent names a
  channel; the card has nowhere else to go.

### Posting the card

- The agent calls `expedite_pr_approval` with the PR URL whenever it decides to; the
  backend decides eligibility and posts the card in that call. Nothing is posted in
  the background.
- Eligible: 1–20 changed lines, every file with a text diff. Binaries and anything
  GitHub cannot show a patch for are refused, because the card could not show the
  voters what they are approving. 20 is what the agent is told; 25 is what is
  enforced, since bouncing a change that lands a few lines over costs more than the
  slack costs the voters.
- Test files sit outside both gates: they do not count toward the limit and they may
  arrive without a patch. CI judges tests, and counting them would price a small fix
  out of shipping with its tests.
- The card draws the source diff. It draws the test diff too when the change is
  test-only or test lines are the majority, because otherwise there would be nothing
  to look at; when tests are the minority of a mostly-source change it names them
  instead, so the thing being voted on stays readable.
- A draft pull request is marked ready for review when its card is posted.
- No path is refused for being sensitive. A denylist is incomplete by construction,
  so it stops nobody deliberate, while matching path segments blocks unrelated files
  that merely contain a word like `token`. The controls that hold are a diff small
  enough to read in full, two distinct people, real GitHub reviews, and the
  repository's own branch protection on the merge.
- An approval row pins the PR, the head SHA the card was posted for, and a
  fingerprint of the files the card drew and their patches. One open card per PR.
  Calling the tool again for an unchanged diff returns the open card; a changed diff
  closes the old card and posts a new one.

### Slack card

PR link, revision, author, the diff as the card draws it, the vote tally, and two
buttons: **Approve** and **Reject and give feedback**. It is posted in the thread only,
never broadcast to the channel. Reactions are never votes.

### Voting

- Voter: a person in the users table, reached through their Slack identity, whose
  GitHub identity has write or higher on the repo. Votes are keyed by user id, so one
  person cannot vote twice through two handles.
- A click is recorded and the card re-rendered; nothing is sent to GitHub. A
  non-author without a stored GitHub token is refused at click time, since their
  review could not be submitted later.
- When the second distinct approval lands, the agent is woken once so it can try the
  merge. The clicker gets an ephemeral confirmation; nothing else is posted.

### Rejection

Any voter can reject while the card is open. A modal collects optional feedback. The
card closes and its votes no longer count. The feedback is sent to the agent once,
attributed, as user input rather than instructions. The agent can post a fresh card.

A Slack rejection is a veto of the vote, not a GitHub `REQUEST_CHANGES`.

### Merge

The agent calls `merge_expedited_pr` once it believes the PR is ready, typically when
a `/baby-sit` watch wakes it with `/baby-sit --ready` or when the card reaches two
approvals. The tool:

1. Re-reads the PR. Merged → card marked merged. Closed → card closed.
2. Recomputes the fingerprint for the current head. A mismatch means a commit changed
   what voters saw: the card is marked superseded and the agent is told to post a new
   one. A commit that touched only files the card did not draw, such as minority
   tests, keeps the votes.
3. Needs two distinct approvals, and readiness: open, not a draft, conflict-free, no
   check running, every required check passed, no unresolved review thread, no
   standing request for changes, and, where Open SWE auto-review is enabled, an Open
   SWE review for this exact head SHA. Anything missing is returned to the agent and
   nothing is written.
4. Submits a GitHub `APPROVE` review at the current head for each non-author approver
   with that person's own token, recording the review id and SHA so a retry does not
   resubmit it. The author's approval counts but is never sent to GitHub.
5. Merges with a GitHub App token scoped to contents and pull requests on that
   repository, conditional on the current head SHA, using a merge method the
   repository allows. GitHub refusing leaves the card open and the reason goes back
   to the agent. No admin bypass, ever.

On a confirmed merge: card → merged, merged reaction on the Slack root. The thread
resolves through the existing merged-PR handling.

### Storage

PostgreSQL, alongside the pull request and users tables: one row per card, one per
vote. A vote records its decision, feedback, and, once submitted, the GitHub review id
and the SHA it was submitted on. Votes reference `users.id`, never a GitHub or Slack
handle; handles are looked up for display only.

### Non-goals

- Auto-enrolling small PRs; line count is not a safety test.
- Admin bypass, bot-authored approvals, weaker protection rules.
- Per-repository enablement or vote expiry, until experience shows a need.

## Security and privacy

- The agent posts the card and asks to merge; it cannot vote, and the merge tool
  enforces quorum, the fingerprint, and readiness itself.
- Feedback is user input, not instructions. Approvals, voters, review ids, and
  outcomes are stored.

## Alternatives

- **Admin merge after two clicks:** bypasses repo guarantees.
- **Reactions as votes:** ambiguous, not revision-bound.
- **A background watcher that posts and merges on its own:** it posted outcome notices
  into threads with no visible context, and took the timing out of the agent's hands.

## Resolved questions

- **Author consent counts toward the quorum.** Two distinct people confirm review;
  the author is usually one of them.
- **Eligibility policy:** the fixed rules above; no preview size cap beyond Slack's.
- **Vote expiry:** none.

## Open questions

- Which third-party review agents expose a trustworthy completion signal, and
  whether to require a configured list of reviewer check names.
