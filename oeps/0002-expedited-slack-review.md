# OEP-0002: Expedited Slack review for small pull requests

- **Authors:** Ramon Nogueira (`@ramon-langchain`)
- **Status:** Draft
- **Created:** 2026-09-09
- **Discussion:** https://github.com/langchain-ai/open-swe/issues/2569
- **Supersedes:** None

## Summary

The agent nominates a tiny PR. When CI is green and all review agents are done and
clean, Open SWE posts the full diff in Slack with approve/reject buttons. Two
authorized people approve; their clicks become real GitHub reviews; Open SWE
merges and resolves the thread. Any rejection kills the round.

## Motivation

For a 5-line change, opening GitHub takes longer than reviewing it. The reviewers
are already in the Slack thread.

## Proposal

### Enrollment

- Off by default; enabled per repository.
- The agent calls an enrollment tool; the backend decides eligibility.
- Eligible: 1–10 changed lines, complete text diff. Never: auth, secrets,
  workflows, dependencies, migrations, binaries.
- A round pins repo, PR, head SHA, and diff fingerprint.

### Readiness

Reuse baby-sit's PR watching, extended to successes and review events. Controls
appear only when, for the enrolled revision:

- PR open, ready, conflict-free;
- every check passed (pending/skipped/neutral block);
- every review agent, including Open SWE's, reports completion — silence is not
  completion, and a passing review check is not "no findings";
- no unresolved review threads, findings, or change requests.

The agent is woken once to publish the card. Everything after is backend state.

### Slack card

PR link, files, full diff, revision, voters, and three buttons: **Approve**,
**Reject and give feedback**, **Use normal review**. Reactions are never votes.

### Voting

- Voter: linked GitHub account with write+ on the repo. Distinct GitHub IDs.
- Each non-author approve submits a GitHub `APPROVE` review at `commit_id` with
  that user's own token. The button says so. Counts only after GitHub confirms.
  No token → reconnect; never a bot identity.
- The author's click counts in Slack but GitHub rejects self-approval, so branch
  protection may still want another reviewer. Show what's missing.
- Two votes are necessary, not sufficient. Prior GitHub approvals don't count.

### Rejection

Any voter can reject at any time. Votes void, card disabled forever. Feedback via
modal or a thread reply from the rejector is posted with attribution and sent to
the agent once. Re-enrollment reruns readiness and needs two fresh votes; an
unchanged diff also needs the rejector to withdraw.

A Slack rejection is a veto of the round, not a GitHub `REQUEST_CHANGES`.
New commit, new finding, or regressed check also kills the round.

### Merge

Quorum → revalidate → persist intent → normal merge conditional on head SHA with a
narrowly scoped credential. GitHub says no → stop. No admin bypass, ever.

Rejection wins if committed before the merge call; after that the card says
"merging". Treat an HTTP error as unknown, not failed.

On confirmed merge: card → merged, merged reaction on the Slack root, watches
dropped, thread resolved via tracked-PR `resolves_thread`.

### Non-goals

- Auto-enrolling small PRs; line count is not a safety test.
- Admin bypass, bot-authored approvals, weaker protection rules.
- Implementing anything in this PR.

## Security and privacy

- The agent nominates; it cannot vote or skip the gate.
- Feedback is user input, not instructions. Rounds, voters, review IDs, and
  outcomes are audited.

## Alternatives

- **Admin merge after two clicks:** bypasses repo guarantees.
- **Reactions as votes:** ambiguous, not revision-bound.
- **Resume the agent on every event:** puts a security state machine in an LLM.

## Unresolved questions

- Does author consent count toward the quorum?
- Eligibility policy, preview size cap, round expiry.
- Which review agents expose a trustworthy completion signal?
