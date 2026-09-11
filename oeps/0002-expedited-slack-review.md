# OEP-0002: Expedited Slack review for small pull requests

- **Authors:** Ramon Nogueira (`@ramon-langchain`)
- **Status:** Draft
- **Created:** 2026-09-09
- **Discussion:** https://github.com/langchain-ai/open-swe/issues/2569
- **Supersedes:** None

## Summary

Let the agent nominate a tiny PR for review in Slack. When CI is green and every
review agent is done and clean, Open SWE posts the full diff with approve/reject
buttons. Two distinct authorized people approve; their clicks become real GitHub
reviews; Open SWE merges normally and resolves the thread. Any rejection kills the
round.

## Motivation

For a 5-line change, opening GitHub takes longer than reviewing it. The reviewers
are already in the Slack thread. Open SWE already has the pieces: baby-sit's
durable PR watching, signed Slack buttons, linked GitHub identities, reviewer
state, and tracked-PR thread resolution.

## Proposal

### Enrollment

- Off by default; enabled per repository.
- At task completion the agent calls an enrollment tool with a one-line reason.
  The backend decides eligibility, not the agent.
- Eligible: 1–10 added+deleted lines, complete text diff. Ineligible regardless of
  size: auth, secrets, workflows, dependencies, migrations, binaries.
- Enrollment pins repo, PR, head SHA, diff fingerprint, Slack location, and Open
  SWE thread. Every round has its own ID.

### Readiness

Generalize baby-sit's subscription infrastructure to watch checks, statuses,
reviews, review threads, comments, and PR revisions — successes as well as
failures. A periodic reconcile repairs missed events without running the agent.

Approval controls appear only when, for the enrolled revision:

- the PR is open, ready, conflict-free, and still eligible;
- every check has passed (pending, skipped, neutral, unknown all block);
- every configured review agent, including Open SWE's, has finished this
  revision — silence is not completion;
- no unresolved review threads, findings, or change requests remain.

Open SWE's review check passes even with findings, so its conclusion alone is not
a clean signal; the reviewer must expose explicit completion.

When ready, the agent is woken once to call a publish tool that reruns the gate and
posts the card. Everything after that is backend state, not agent turns.

### Slack card

PR link, files, full unified diff (attach a patch if needed; never approve over a
truncated diff), revision, check summary, current voters, and three buttons:

- **Approve this revision**
- **Reject and give feedback**
- **Use normal review**

Reactions are never votes.

### Voting and GitHub reviews

- Authorized voter: a human with a linked GitHub account and write+ access on the
  repo. Thread participation is not required. Count distinct GitHub user IDs.
- Each non-author approve click submits `APPROVE` at `commit_id` via that user's
  own token ([review API](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request)).
  The button says so. The vote counts only after GitHub confirms. No token → ask to
  reconnect; never substitute a bot identity.
- The author may count toward the two Slack votes, but GitHub
  [rejects self-approval](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews),
  so branch protection may still need another reviewer. Show what's missing.
- Two Slack votes are necessary, not sufficient. Prior GitHub approvals do not
  count toward the round.

### Rejection

Any authorized voter can reject at any time, even after approving.

1. Persist the rejection, void all votes, disable the card. It never reactivates.
2. Open a feedback modal and accept a thread reply from the rejector as an
   alternative. Cancelling the modal does not undo the rejection.
3. Post the reason in the thread, attributed to the human, and dispatch it to the
   agent exactly once.
4. The agent may re-enroll after addressing it: full readiness rerun, two fresh
   votes. An unchanged diff also needs the rejector to clear their objection.

A Slack rejection is a veto of the round, not a GitHub `REQUEST_CHANGES`. Existing
GitHub change requests still block and are never dismissed automatically.

A new commit, new finding, or regressed check also invalidates the round and
disables the card. Re-enrollment is explicit.

### Merge

Quorum → revalidate everything → persist merge intent → normal merge conditional
on head SHA, using the repo's allowed method and a narrowly scoped credential.
GitHub says no → stop. Never fall back to admin bypass.

Rejection wins if committed before the merge call. Once the call is sent it cannot
be recalled; the card says "merging". Persist intents before external calls,
dedupe retries, and treat an HTTP error as "unknown", not "did not happen".

Merge queues: admission is not completion; keep tracking and withdraw on
invalidation. Repos whose queue can't honor that are out of scope initially.

On confirmed merge: mark the card merged, add the merged reaction to the Slack
root, drop watches, and resolve the Open SWE thread via tracked-PR
`resolves_thread` (hidden, not deleted). A closed-unmerged PR gets no reaction.

### Non-goals

- Auto-enrolling every small PR; line count is not a safety test.
- Admin bypass, bot-authored approvals, or weaker protection rules.
- Making Slack vetoes binding on maintainers merging elsewhere.
- Implementing anything in this PR.

## Security and privacy

- The agent can nominate; it cannot vote, pick identities, or skip the gate.
- Verify Slack signatures, round/message identity, account mapping, token
  identity, and live repo permissions on every click. Open SWE admin ≠ GitHub
  approver.
- Watch and merge credentials get minimum repo permissions and must not bypass
  protections; verify before enabling a repo.
- The diff is repo data: the channel must be approved for that repo's visibility.
  Escape diff and feedback text; feedback is user input, not instructions.
- Store round ID, revision, voters, rejection reasons, GitHub review IDs, and
  merge outcome for audit.

## Alternatives

- **Admin merge after two clicks:** bypasses repo guarantees; needs broad creds.
- **One bot approval "for two reviewers":** misattributes who approved.
- **Two non-author voters only:** simpler; excludes author consent. Open question.
- **Reactions as votes:** ambiguous, not revision-bound.
- **Resume the agent on every event:** slow, costly, puts a security state machine
  in an LLM.

## Unresolved questions

- Does author consent count toward the Slack quorum?
- Initial eligibility policy, preview size cap, and round expiry.
- Which review agents expose trustworthy completion signals?
- Merge-queue support in the first release?
