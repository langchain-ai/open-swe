# OEP-0002: Expedited Slack review for small pull requests

- **Authors:** Ramon Nogueira (`@ramon-langchain`)
- **Status:** Draft
- **Created:** 2026-09-09
- **Discussion:** https://github.com/langchain-ai/open-swe/issues/2569
- **Supersedes:** None

## Summary

Allow an agent to nominate a small, low-risk pull request for expedited review in
Slack. Once checks and automated reviews are complete and clean, publish its full
diff with approval and rejection buttons. At least two distinct authorized people
must approve the current round before Open SWE attempts a normal GitHub merge.

Slack is the review interface, not a way around GitHub protections. Non-author
approvals become real GitHub reviews through each person's linked account. A
rejection ends the round immediately; feedback returns to the agent, and any
subsequent round starts with no votes. On confirmed merge, update the Slack
message, react to the original thread, and resolve the Open SWE job without
another agent turn.

This OEP proposes product and trust boundaries, not a commitment to specific tool
names or storage schemas. Publishing this draft does not accept or implement it.

## Motivation

For tiny changes, opening GitHub and finding the relevant diff can take longer
than reviewing the change. The work and its participants are often already in
Slack. Presenting a complete, revision-bound diff there can shorten the approval
loop without removing human review.

Open SWE already has durable CI monitoring in baby-sit, signed Slack interactions,
linked GitHub identities, automated reviewer state, and tracked-PR thread
resolution. These pieces should support a deliberate workflow rather than an
agent polling indefinitely or interpreting ordinary reactions as merge consent.

## Proposal

### Eligibility and enrollment

The feature is disabled by default and enabled per repository. At task completion,
the agent may call an enrollment tool for a PR tracked by the current Open SWE
thread, supplying a short explanation of why expedited review is appropriate.
The backend, not the agent's assertion, enforces eligibility and authorization.

The initial scope is 1–10 total added plus deleted lines in a complete text diff.
Size is a ceiling, not proof of low risk. Authentication, authorization, secrets,
workflow, dependency, migration, binary, and otherwise high-risk changes use
normal review. A truncated or impractically large preview is ineligible even if
its changed-line count is small. Repository policy may narrow this scope.

Enrollment binds the repository, PR, original Slack location, Open SWE thread,
head SHA, base/diff fingerprint, and policy version. Each approval round has a
unique identity, even when a later round reviews the same commit. Draft PRs can
be monitored, but must explicitly be marked ready and complete resulting checks
and reviews before approval controls appear.

### Event-driven readiness

Generalize baby-sit's durable subscription infrastructure: PR-to-thread routing,
event deduplication, distributed serialization, and scheduled reconciliation.
Keep baby-sit's retry policy separate from expedited review's merge policy, and
preserve existing watches and scheduler entrypoints during migration.

Watch checks, commit statuses, PR lifecycle and revision changes, review events,
review-thread resolution, and PR comments, including events without an Open SWE
mention. Open SWE's own reviewer should expose completion for the reviewed
revision. Successful events matter as much as failing ones; the current baby-sit
failure-only webhook path is insufficient. A periodic fallback repairs missed
events without requiring an agent run for every evaluation.

Before publishing approval controls, the backend must establish:

- The PR is open, ready for review, conflict-free, and still eligible at the
  enrolled revision. Missing human approvals alone do not prevent showing the
  controls that collect those approvals.
- The complete, paginated check set for the current revision is known, its
  aggregate is successful, and every required check has passed. Pending, missing,
  cancelled, failed, or unknown states block. Skipped and neutral results are not
  passes; only explicitly recognized informational checks may be exempted.
- Every configured review agent, including Open SWE, has finished reviewing this
  revision successfully. Silence is not proof that a reviewer ran. A reviewer
  without a reliable completion signal makes the PR ineligible.
- There are no outstanding review findings, unresolved review threads, or active
  change requests from humans or bots. Inspect top-level comments and review
  bodies too, not just inline threads. Resolved historical feedback and known
  informational messages do not block; ambiguous reviewer output does.

Open SWE's review check currently succeeds even when it surfaces findings. Its
check conclusion alone cannot establish that the review is clean. Check settling
also cannot substitute for explicit reviewer completion.

When ready, the subscription wakes the originating agent once for that round.
The agent can call a publish-approval tool, which reruns the readiness gate and
builds the message from authoritative state. After publication, approvals,
merging, and cleanup are backend operations, not new agent turns. Rejection with
feedback is the deliberate exception.

### Slack review and GitHub approval

The message includes the PR link, filenames, full unified diff with enough
context to review, revision, check/reviewer summary, named voters, and the
following controls:

- **Approve this revision**: record this person's consent for this round.
- **Reject and give feedback**: end the round and request a reason.
- **Use normal review**: leave expedited mode without asserting a code defect.

Attach a complete patch file when necessary. Do not enable approval over a
truncated preview, and do not put private code in a publicly accessible artifact.
Ordinary thumbs-up reactions remain feedback, not approval votes.

An authorized voter is a human with an active Slack-to-GitHub mapping and current
write, maintain, or admin access to the repository. They need not own or have
previously participated in the Open SWE thread. Count distinct GitHub user IDs,
not display names, repeated clicks, or multiple Slack identities for one account.

GitHub's [review API](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request)
accepts `event: APPROVE` and `commit_id`. For each non-author click, submit the
review using that person's linked, refreshable GitHub user token and retain the
returned review ID. The button must disclose that it submits a GitHub approval
and authorizes merging once the remaining requirements are met. Count the vote
only after confirming the review was submitted successfully. A missing token
requires reconnection; never silently substitute a bot identity.

The PR author may count toward the two-person Slack quorum, but GitHub
[does not allow authors to approve their own PRs](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews).
Their click records Slack consent only. Two Slack votes are necessary, not always
sufficient: required non-author approvals, CODEOWNERS, and other repository rules
still apply. Show the remaining requirement rather than treating a third click
as an error. Existing GitHub approvals do not replace the requirement for two
explicit clicks in the current Slack round.

### Rejection and re-entry

Any authorized voter can reject, regardless of the number of approvals already
collected or whether they previously approved. A rejection must not depend on
first completing a modal or posting a comment:

1. Persist the rejection, invalidate every local vote, and disable approval for
   that round. An old message can never become active again.
2. Offer a feedback modal and request a reply in the original Slack thread.
   Cancelling the modal does not undo the rejection. An empty rejection leaves
   the round stopped and waiting for feedback; it does not start a speculative
   agent run.
3. On modal submission, publish the reason in the original thread with explicit
   attribution to its human author. Alternatively, accept a thread reply from
   that rejecting user. A scoped pending-feedback marker lets this reply trigger
   the agent without requiring a mention, including in a multi-person thread.
   Unrelated replies do not gain this behavior. A further rejector's feedback is
   also preserved and delivered, not discarded by deduplication.
4. Dispatch feedback to the original agent exactly once per submission. The agent
   addresses it, asks for clarification, or leaves the PR in normal review. It
   must not infer withdrawal of an objection from silence or lack of code changes.
5. The agent may explicitly enroll a new round after addressing the feedback.
   Rerun every readiness check and require two fresh votes. Re-entry for an
   unchanged diff additionally requires the rejecting users to explicitly clear
   their objections. The agent cannot restart an unchanged proposal repeatedly
   to bypass a rejection.

A Slack rejection is a veto of this expedited round, not an automatic GitHub
`REQUEST_CHANGES` review. Posting the latter would impose a different unblock
process, often requiring the same reviewer to approve again. Existing GitHub
change requests still block and are never automatically dismissed. Submitted
GitHub approvals may remain visible, but no approval from an earlier round counts
toward the new local quorum.

A head/base/diff change, new finding, or regressed check also invalidates the
round. The backend disables the message immediately and does not resume the
coding agent just to collect approvals again. Explicit re-enrollment starts a
new readiness cycle; an explicit feedback message can resume work as above.

### Merge and completion

The following transitions define the important boundaries:

| State | Next action |
|---|---|
| Monitoring | Wait for readiness; dispatch one publication opportunity. |
| Awaiting approval | Accept votes or end the round on rejection/invalidation. |
| Rejected | Wait for feedback or explicit withdrawal; never reuse old votes. |
| Invalidated | Require explicit re-enrollment and a fresh round. |
| Merging | Quorum reached and final gates passed; reconcile the GitHub operation. |
| Merged | Update Slack, stop subscriptions, and resolve the completed job. |
| Stopped | Normal review, expiry, disabled policy, or PR closed without merge. |

Serialize vote, rejection, and merge transitions across workers. Immediately
before merging, revalidate the current revision, voters, GitHub reviews, check
set, outstanding feedback, and policy. Persist merge intent and make a normal
merge request conditional on the expected head SHA, using the repository's
allowed merge method and a narrowly scoped backend credential. Never fall back
to admin privileges when GitHub rejects the request.

Rejection wins if committed before the final merge transition. Once a merge
request has been sent to GitHub, it cannot reliably be recalled; controls must
show that merging is in progress rather than promise a veto. Report a late
rejection honestly. Likewise, GitHub cannot atomically enforce every Slack-side
predicate: final reads and head-SHA conditions reduce races but do not prevent a
new external comment arriving during the merge request.

Honor merge queues where required. Queue admission is not merge completion;
continue tracking the revision and eligibility, and withdraw a queued request
when invalidated where supported. Repositories whose queue behavior cannot
preserve these guarantees remain outside the initial rollout.

Persist review and merge intents before external calls, deduplicate callback
retries, and reconcile timeouts before repeating a side effect. Do not assume
that an HTTP error means GitHub did not accept the operation. Stale worker results
must not resurrect a rejected round or launch a second merge.

Only a confirmed merge updates the card to merged, adds the configured merged
reaction to the original Slack root, and removes watches and fallback schedules.
Reuse tracked-PR `resolves_thread` behavior: resolve the Open SWE thread only when
all its tracked PRs are terminal and no remaining work belongs to the job. Hide
it from active lists but retain its audit history. Do not delete the thread or
archive the shared Slack channel. Retry notification and cleanup failures without
another agent run or this flow's extra post-merge feedback prompt. A PR closed
without merging must not receive a merged reaction.

### Non-goals

- Automatically enrolling every small PR or treating line count as a safety test.
- Admin bypass, fake human reviews, or relaxing repository protection rules.
- Replacing normal review for complex changes or unsupported reviewer integrations.
- Making Slack vetoes binding on maintainers who merge through other interfaces.
- Implementing the feature as part of this proposal PR.

## Security and privacy

The agent may nominate a PR, but cannot manufacture votes, choose another user's
identity, or override the readiness gate. Verify Slack request signatures and
freshness, workspace and internal-channel eligibility, stored message/round
identity, active account mapping, token identity, and live repository permissions.
An Open SWE workspace-admin role alone does not confer GitHub approval authority.

Resolve each clicking user's token independently of the shared thread's prior
actor. Reuse encrypted credential storage and refresh; keep credentials out of
watch records, Slack payloads, agent context, and logs. Watch credentials and
merge credentials should have only their required repository permissions; the
merge actor must not implicitly bypass protections through its account or App
configuration. Verify this before enabling a repository.

The full diff is repository data. A Slack channel must be approved for that
repository's visibility, not merely free of external guests. Reject ineligible
channels and private/public mismatches. Escape untrusted diff and feedback text;
feedback is attributed user input subject to existing trust rules, not privileged
system instructions. Store auditable round IDs, revisions, voters, rejection
reasons, GitHub review IDs, and merge outcomes with access controls matching the
original job.

## Alternatives

- **Admin merge after two clicks:** makes the local quorum sufficient but bypasses
  repository guarantees and requires broader credentials. Prefer real per-user
  reviews and normal merge; do not use bypass as a hidden fallback.
- **One bot approval claiming two reviewers:** does not create two independently
  attributed GitHub reviews and misrepresents who approved.
- **Require two non-author voters:** simpler alignment with GitHub, but excludes
  useful author consent. The proposed local quorum permits the author while
  clearly preserving GitHub's independent requirements.
- **Use ordinary reactions:** familiar, but ambiguous with evaluation feedback and
  not clearly tied to a revision, round, or explicit merge authorization.
- **Post the diff and finish in GitHub:** the safe fallback, but retains the
  context switch this proposal is intended to remove.
- **Resume the agent on every event or vote:** adds latency and cost and puts a
  security-sensitive state machine in an LLM. Keep lifecycle operations in the
  backend; resume the agent only for publication or deliberate user feedback.

## Unresolved questions

Before acceptance and rollout, settle:

- Whether author consent should count toward the local quorum, or the product
  should require two non-author voters for a simpler promise.
- The initial repository/path eligibility policy, maximum preview size, and
  approval-round expiry. No live round should remain authorized indefinitely.
- Which reviewer integrations have trustworthy completion and finding-resolution
  signals, especially reviewers that publish only top-level comments.
- Which repositories and Slack channels meet the visibility and non-bypassing
  credential requirements, and whether merge-queue support belongs in the first
  release.

## Adoption

If accepted, implement separately behind a per-repository flag. Preserve existing
baby-sit behavior through a compatibility adapter, deploy the listener before
exposing enrollment, and provide a kill switch that invalidates active rounds.

Focused verification must cover concurrent votes/rejections, author consent,
revoked access, stale messages and revisions, feedback routing and deduplication,
re-entry without inherited votes, late reviewer findings despite green checks,
unknown API results, queue invalidation, and idempotent merge cleanup. Exercise
the Slack flow with multiple linked human identities before enabling it for real
merges. No production permissions or repository rules change merely by publishing
this OEP.
