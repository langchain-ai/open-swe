# OEP-0004: Close the reviewer learning loop

- **Authors:** pufuki (`@pufuki`)
- **Status:** Draft
- **Created:** 2026-09-17
- **Discussion:** https://github.com/langchain-ai/open-swe/issues/2893
- **Supersedes:** None

## Summary

The reviewer records finding outcomes and refines a per-repository prompt from
them, but several feedback paths never reach the analyzer. This proposal makes
every terminal finding transition produce exactly one outcome from any actor —
including GitHub reactions and human thread resolutions — with clear provenance,
and gives continual runs the current prompt they are meant to refine.

## Motivation

PR #1365 established the outcomes dataset and deliberately wired emission points
into `update_finding`, `resolve_finding_thread`, and the GitHub reaction
handlers, listing GitHub and Slack reactions as learning signals. Several of
those paths are no longer connected, so genuine human feedback is lost and the
analyzer learns from a partial picture.

**Reactions never arrive.** The supported webhook event list omits `reaction`
(`agent/webhooks/common.py:1005`), so GitHub deliveries are rejected at
`agent/github/routes.py:47` before dispatch. The handlers that exist for
reactions (`agent/github/feedback.py:231`) have no callers, which means the
cheapest and most direct human signal trains nothing.

**Human resolutions are silent.** When a person resolves a finding's review
thread on GitHub, the reconciliation that runs in response updates the finding's
status but records no outcome (`agent/review/reconcile.py:132`). Only the agent's
own tools currently emit, so the most common human action is invisible to the
loop.

**Outcome examples are not unique per finding.** The example identity includes
the label source next to the finding (`agent/utils/reviewer_outcomes.py:95`), so
a single finding can accumulate several examples, and reading outcomes can return
the same finding as both confirmed and dismissed.

**Recorded commit SHAs misclassify resolutions.** The SHA written with an outcome
comes from the run configuration rather than the HEAD observed at resolution
(`agent/utils/reviewer_outcomes.py:263`), even though `update_finding` resolves a
live HEAD (`agent/tools/update_finding.py:150`) and only stores it as
`last_confirmed_sha`. A resolution that involved no new commit can therefore be
labelled as fixed by a commit.

**Continual runs cannot edit the prompt they refine.** The continual analyzer
receives only the repository and mode (`agent/review/style_jobs.py:61`), and its
setup injects sample text without ever loading the stored prompt
(`agent/analyzer.py:110`). The `continual-learning` skill assumes the current
prompt is available.

## Proposal

### One outcome per terminal transition

Every transition of a published finding to a terminal status (`resolved` or
`dismissed`) emits exactly one outcome, regardless of actor: an agent tool,
webhook reconciliation, or a reaction. Provenance stays in `label_source` and
metadata, such as the acting login, the source, and the active reactions, rather
than in the example identity.

### Per-finding identity

Examples are keyed by repository and finding. Each finding holds one current
label, and the most recent terminal transition wins. When outcomes are read back,
the same finding is deduplicated and returned with its side and start and end
lines so the analyzer can quote concrete context.

### SHA semantics

An outcome records `first_seen_sha` as published and the HEAD observed at the
transition. A resolution counts as `resolved_by_commit` only when a commit landed
after the finding was first seen; a resolution with no code change stays
`resolved_same_sha`. Retracting a reaction removes its outcome instead of leaving
a stale label behind.

### Human resolutions

`reconcile_findings_with_review_threads` emits an outcome on the open-to-resolved
transition when it is given the repository, pull request, HEAD, and thread
context. Its existing callers in `agent/github/webhook.py`, `agent/reviewer.py`,
`agent/tools/publish_review.py`, and `agent/tools/resolve_finding_thread.py` pass
that context once.

### Reactions

`reaction` joins the supported webhook events and its created and deleted actions
route to the existing handlers after the repository allowlist and public-repo
organization gate. Bot senders are ignored, and both missing findings and lookup
failures are logged and swallowed so a stale reaction can never fail a webhook.

### Continual prompt context

Continual runs receive the stored prompt and its analysis summary in their
context so they refine the existing prompt instead of rebuilding it. Bootstrap is
unchanged.

### Non-goals

- Changing the outcomes dataset, tenant, or example schema beyond identity and
  deduplication.
- Supporting reaction content beyond 👍 and 👎.
- Applying refined prompts to the reviewer outside the existing review and
  evaluation gating.
- Replacing the analyzer with a separate model-training pipeline.

## Security and privacy

No change to trust boundaries. Reaction webhooks are untrusted input and pass the
same repository allowlist and public-repo organization gate as every other event,
and bot senders are skipped. Attribution stores GitHub logins that are already
recorded today, and outcomes continue to land in the existing LangSmith dataset.

## Alternatives

**Keep per-label-source example identity.** This preserves a fuller history, but
it trains on contradictions when a finding flips labels and cannot represent a
retraction without adding a second row. Per-finding identity with latest-wins
matches the stated intent of one current label per finding.

**Store outcomes in the application database.** This duplicates the dataset the
analyzer and `read_finding_outcomes` already read, and adds migration and
retention work without new capability.

**Poll reactions through the GitHub API instead of webhooks.** This adds
rate-limit pressure and latency for events GitHub already delivers.

## Unresolved questions

- When a reaction is removed after an outcome exists, should the example be
  deleted, or should a neutral marker be written?
- Is an explicit "won't fix" dismissal meaningfully different from a thumbs-down
  false positive, or should both map to `dismissed`?
- Do we migrate existing per-label-source examples, or let new transitions
  overwrite them as they occur?
