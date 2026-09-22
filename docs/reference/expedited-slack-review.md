# Expedited Slack review for small pull requests

This document records the design and operating constraints of expedited Slack review. The feature is experimental and off by default.

## Summary

The agent nominates a tiny PR. Once CI is green and every review is clean, Open SWE
posts the full diff in the Slack thread with Approve and Reject buttons. Two distinct
people with write access approve; non-author clicks become real GitHub reviews; Open
SWE merges. Any rejection, new commit, or regressed check kills the vote.

## Motivation

For a 5-line change, opening GitHub takes longer than reviewing it. The reviewers
are already in the Slack thread.

## Design

### Enablement

- Experimental. Off by default; an admin turns it on per Open SWE instance in
  Settings. Turning it off withdraws open cards and hides the tool.
- Available only in threads that have a Slack location; the card has nowhere else to go.

### Enrollment

- The agent calls `expedite_pr_approval` with the PR URL; the backend decides
  eligibility.
- Eligible: 1–20 changed lines, every file with a text diff. Binaries and anything
  GitHub cannot show a patch for are refused, because the card could not show the
  voters what they are approving.
- Test files sit outside both gates: they do not count toward the limit, they may
  arrive without a patch, and the card names them instead of showing them. CI judges
  tests, and counting them would price a small fix out of shipping with its tests.
- No path is refused for being sensitive. A denylist of sensitive paths was tried and
  dropped: it is incomplete by construction, so it stops nobody deliberate, while
  matching path segments blocks unrelated files that merely contain a word like
  `token`. The controls that hold are the ones that do not depend on guessing which
  files matter — a diff small enough to read in full, two distinct people, real
  GitHub reviews, and the repository's own branch protection on the merge.
- An approval pins repo, PR, head SHA, and a fingerprint of the diff. One active
  approval per PR.

### Readiness

Open SWE watches the PR through GitHub webhooks with a cron fallback. The card
appears only when, for the enrolled revision:

- the PR is open, not a draft, and conflict-free;
- no check is still running, and every check GitHub requires has passed. A failing
  check GitHub does not require is named on the card instead of blocking it, so the
  voters decide with it in front of them;
- no review thread is unresolved and nobody has a standing request for changes;
- where Open SWE auto-review is enabled for the repository, Open SWE has published a
  review for this exact head SHA. Silence is not completion.

Third-party review agents are covered through their check runs and review threads;
no other completion signal exists for them.

Everything after enrollment is backend state. The agent is woken only for an outcome
it must act on.

### Slack card

PR link, revision, author, every changed file with its full patch, the vote tally,
and two buttons: **Approve** and **Reject and give feedback**. Reactions are never
votes.

### Voting

- Voter: a person in the users table, reached through their Slack identity, whose
  GitHub identity has write or higher on the repo. Votes are keyed by user id, so
  one person cannot vote twice through two handles.
- The author may approve; that click counts in Slack but is not sent to GitHub.
- Every non-author approve submits a GitHub `APPROVE` review at `commit_id` with that
  user's own token, and counts only after GitHub confirms. No token → sign in to the
  dashboard; never a bot identity.
- Two approvals are necessary, not sufficient. Prior GitHub approvals don't count.

### Rejection

Any voter can reject at any time. A modal collects optional feedback. Votes void,
card disabled forever. The feedback is sent to the agent once, attributed, as user
input rather than instructions. Re-enrollment reruns readiness and needs two fresh
votes.

A Slack rejection is a veto of the vote, not a GitHub `REQUEST_CHANGES`. A new
commit, changed diff, or regressed check also kills the vote.

### Merge

Quorum → revalidate readiness and the pinned diff (retargeting the base branch
changes the diff without changing the head SHA) → merge with a GitHub App token scoped to contents
and pull requests on that repository, conditional on the reviewed head SHA, using a
merge method the repository allows. GitHub says no → the vote fails and the agent is
told why. No admin bypass, ever.

A rejection committed before the merge call wins. A transport error leaves the vote
in `merging`; the next evaluation either confirms the merge or retries it. A
transient blocker sends it back to `open` with its votes intact, and readiness
recovering resumes the merge — nobody can vote it forward from there, because the
two people who already approved are the ones being refused.

On confirmed merge: card → merged, merged reaction on the Slack root, watch dropped.
The thread resolves through the existing merged-PR handling.

### Storage

PostgreSQL, alongside the pull request and users tables: one row per approval, one
per vote. Votes reference `users.id`, never a GitHub or Slack handle; handles are
looked up for display only.

### Non-goals

- Auto-enrolling small PRs; line count is not a safety test.
- Admin bypass, bot-authored approvals, weaker protection rules.
- Per-repository enablement or vote expiry, until experience shows a need.

## Security and privacy

- The agent nominates; it cannot vote or skip the gate.
- Feedback is user input, not instructions. Approvals, voters, review ids, and
  outcomes are stored.

## Alternatives

- **Admin merge after two clicks:** bypasses repo guarantees.
- **Reactions as votes:** ambiguous, not revision-bound.
- **Resume the agent on every event:** puts a security state machine in an LLM.

## Resolved questions

- **Author consent counts toward the quorum.** Two distinct people confirm review;
  the author is usually one of them.
- **Eligibility policy:** the fixed rules above; no preview size cap beyond Slack's.
- **Vote expiry:** none.

## Open questions

- Which third-party review agents expose a trustworthy completion signal, and
  whether to require a configured list of reviewer check names.
