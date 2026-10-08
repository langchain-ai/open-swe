# Expedited Slack review for small pull requests

This document records the design and operating constraints of expedited Slack review. The feature is experimental and off by default.

## Summary

The agent posts a tiny PR's full diff in the Slack thread. For a draft, the PR's
author first marks it ready from an author-only Slack DM. Then one person other than the author
approves, and the click submits that person's GitHub review at once. Once checks and
reviews are clean, the agent calls a merge tool that merges. The approval survives a
later commit only if it leaves the diff shown on the card unchanged.

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
- Hunks that qualify under the target repository's `.open-swe/APPROVALS.md` (read at
  the PR's base commit) sit outside both gates too, so a PR of any size qualifies when
  all but a small part of it would be auto-approved. The agent names them in
  `excluded` (path, hunk start lines or the whole file, the guideline, and why); the
  backend trusts that judgment. Each call that excludes something writes every
  excluded hunk, its guideline and reason, the base and head SHAs, and a hash of
  APPROVALS.md to the audit log. The card lists exclusions by guideline with line
  counts, and a PR whose drawn, excluded and test lines do not add up to its total is
  refused, so no change is ever neither drawn nor listed. They stay in the fingerprint: a commit that changes an excluded hunk is a
  diff change, and the changed hunk no longer matches its exclusion, so the card draws
  it.
- The card draws the source diff. It draws the test diff too when the change is
  test-only or test lines are the majority, because otherwise there would be nothing
  to look at; when tests are the minority of a mostly-source change it names them
  instead, so the thing being voted on stays readable.
- A draft stays a draft. **Mark ready for review** is sent by DM
  to the PR's linked Slack author, rather than an ephemeral thread message. It undrafts
  the PR with the author's own GitHub token and then opens the shared card for approval.
  Calling the tool again retries this private prompt. If Slack delivery fails or the
  author is not linked, the tool reports that they must mark it ready on GitHub;
  calling the tool again after that opens the existing card for approval.
- No path is refused for being sensitive. A denylist is incomplete by construction,
  so it stops nobody deliberate, while matching path segments blocks unrelated files
  that merely contain a word like `token`. The controls that hold are a diff small
  enough to read in full, a reviewer who is not the author, real GitHub reviews, and
  the repository's own branch protection on the merge.
- An approval row pins the PR, the head SHA the card was posted for, and a
  fingerprint of the files the card drew and their patches. One open card per PR.
  Calling the tool again for an unchanged diff returns the open card; a changed diff
  closes the old card and posts a new one.

### Slack card

PR link, author, the diff as the card draws it, and its status. A draft's shared card
shows a waiting status and **Dismiss**, never the author-only readiness button;
otherwise it has **Approve** and **Dismiss**. Once approved, the diff and buttons go and
the card says who approved. Once merged or cancelled, the whole card becomes one line,
such as *Expedited review: merged* or *Expedited review: dismissed by @someone*, and
the PR link. Reactions are never votes.

When `reviewChannel` is configured in `.open-swe/settings.json`, the ready card is
automatically broadcast there, without send controls. Drafts wait until the author
marks them ready. The destination must be public and not externally shared.

Otherwise the card is posted in the thread only. Once it is open for approval it can
be sent to one channel, once:

- When the card is posted, it lists its thread's channel, then the public, not
  externally shared channels where the PR author's non-private Open SWE threads ran
  in the last 14 days, then channels the author's cards were sent to in the last
  90 days. There are at most 10.
- With only the thread's channel on the list, the card shows **Broadcast in #channel**
  and **Other channel…**. With more, it shows a dropdown (thread's channel preselected,
  **Other…** last) and **Send**. **Other** opens a picker of public channels, and a
  channel picked there is on the list for the author's later cards.
- Anyone in the thread may send the card to the thread's own channel. This reposts
  the card as a thread reply also sent to the channel.
- Only a voter (see below) may send it to another channel, since that shows the diff
  to new people. The card is posted at the top of that channel with a link back to
  the thread, and votes work from either copy.

When the card is approved or closes for any reason, both the broadcast and the copy in
the other channel are deleted, and the card stays in the thread only. So no channel
keeps a finished card.

### Voting

- Voter: a person in the users table, reached through their Slack identity, whose
  GitHub identity has write or higher on the repo. Votes are keyed by user id, so one
  person cannot vote twice through two handles.
- The author cannot approve their own PR. One approval from anyone else is enough.
- A click is recorded, the card re-rendered, and a GitHub `APPROVE` review submitted
  with the voter's own token on the current head, provided the diff the card drew is
  unchanged there. The review body links the Slack thread. A voter without a stored
  GitHub token is refused. If GitHub is unavailable or refuses, the vote stays
  recorded and the merge submits it.
- When the approval lands, the agent is woken once so it can try the merge. The
  clicker gets an ephemeral confirmation; nothing else is posted.

### Dismissal

Anyone in Slack may dismiss an open card, with no GitHub link or write access needed.
The card is cancelled and its votes no longer count. Nothing is sent to the agent, and
the GitHub reviews the votes submitted are dismissed: anyone who wants changes tags the
agent in the thread like any other request, and it can post a fresh card afterwards.
Every card that closes without a merge, superseded ones and a closed PR's included,
dismisses its reviews the same way, so a reopened PR does not inherit them. A review
counts as dismissed only once GitHub confirms; one GitHub refused is retried whenever
another card of the PR closes.

### Merge

The agent calls `merge_expedited_pr` once it believes the PR is ready, typically when
a `/baby-sit` watch wakes it because checks went green, or when someone approves the
card. The tool:

1. Re-reads the PR. Merged → card marked merged. Closed → card closed.
2. Recomputes the fingerprint for the current head. A mismatch means a commit changed
   what voters saw: the card is marked superseded and the agent is told to post a new
   one. A commit that touched only files the card did not draw, such as minority
   tests, keeps the votes.
3. Needs one approval from someone other than the author, and readiness: open, not a
   draft, conflict-free, every required check reported, no
   check running, every required check passed, no unresolved review thread, no
   standing request for changes, and, where Open SWE auto-review is enabled, an Open
   SWE review for this exact head SHA. Anything missing is returned to the agent and
   nothing is written.
4. Submits a GitHub `APPROVE` review at the current head for any approver whose
   review from the click is not a standing approval: it never landed, or GitHub
   dismissed it as stale after a later commit.
5. Merges with a GitHub App token scoped to contents and pull requests on that
   repository, conditional on the current head SHA, using a merge method the
   repository allows. GitHub refusing leaves the card open and the reason goes back
   to the agent. No admin bypass, ever.

On a confirmed merge: card → merged, merged reaction on the Slack root. The thread
resolves through the existing merged-PR handling.

When someone merges or closes the PR on GitHub themselves, the `pull_request` closed
webhook settles an open card the same way, from the PR's current state rather than the
event, so a late delivery cannot close the card of a reopened PR: merged → card merged
and the merged reaction; closed → card closed and its reviews dismissed. Nothing new is
posted in Slack and the agent is not woken.

### Storage

PostgreSQL, alongside the pull request and users tables: an expedited card is a
[human review request](human-review.md) of kind `expedited`, and each vote is one of its
participants. A vote records its decision and, once submitted, the GitHub review id
and the SHA it was submitted on. Votes reference `users.id`, never a GitHub or Slack
handle; handles are looked up for display only.

### Non-goals

- Auto-enrolling small PRs; line count is not a safety test.
- Admin bypass, bot-authored approvals, weaker protection rules.
- Per-repository enablement or vote expiry, until experience shows a need.

## Security and privacy

- The agent posts the card and asks to merge; it cannot vote, and the merge tool
  enforces the non-author approval, the fingerprint, and readiness itself.
- Approvals, voters, review ids, and outcomes are stored.

## Alternatives

- **Admin merge on a click:** bypasses repo guarantees.
- **Reactions as votes:** ambiguous, not revision-bound.
- **A background watcher that posts and merges on its own:** it posted outcome notices
  into threads with no visible context, and took the timing out of the agent's hands.

## Resolved questions

- **One reviewer, never the author.** The author's say is marking the draft ready;
  approval comes from someone else.
- **Eligibility policy:** the fixed rules above; no preview size cap beyond Slack's.
- **Vote expiry:** none.

## Open questions

- Which third-party review agents expose a trustworthy completion signal, and
  whether to require a configured list of reviewer check names.
