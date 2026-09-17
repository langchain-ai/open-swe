# OEP-0004: Close the reviewer learning loop

- **Authors:** pufuki (`@pufuki`)
- **Status:** Draft
- **Created:** 2026-09-17
- **Discussion:** https://github.com/langchain-ai/open-swe/issues/2893
- **Supersedes:** None

## Summary

The reviewer already records finding outcomes (`resolved-by-commit`, `dismissed`,
GitHub/Slack 👍/👎) and refines a per-repo prompt from them, but several feedback
paths never reach the analyzer. This proposal makes every terminal finding
transition produce exactly one outcome from any actor — including GitHub
reactions and human thread resolutions — with unambiguous provenance, and gives
continual runs the current prompt they are supposed to be editing.

## Motivation

#1365 built the outcomes dataset and deliberately wired emit points into
`update_finding`, `resolve_finding_thread`, and the reaction handlers. The
remaining gaps make that investment partially inert:

- **Reactions never arrive.** `SUPPORTED_GH_EVENTS`
  (`agent/webhooks/common.py:1005`) omits `reaction`, so the webhook is rejected
  at `agent/github/routes.py:47` before dispatch. `process_github_reaction_added`
  / `process_github_reaction_removed` (`agent/github/feedback.py:231`) have no
  callers. The cheapest, most direct human signal trains nothing.
- **Human resolutions are silent.** `_sync_thread_status`
  (`agent/review/reconcile.py:132`) flips a finding to `resolved` at line 153
  without emitting an outcome. Only agent tool calls emit, so a person resolving
  a thread on GitHub — the common case once review is published — is invisible.
- **Examples collide per finding.** `_example_id`
  (`agent/utils/reviewer_outcomes.py:95`) keys on
  `(repo, finding_id, label_source)`. One finding can therefore produce several
  examples, and `read_outcomes_for_repo` (line 285) can surface the same finding
  as both confirmed and dismissed.
- **SHA provenance mislabels.** `emit_finding_status_outcome` reads `cfg.head_sha`
  (line 263). `update_finding` resolves a live head SHA (line 150) but only stores
  it as `last_confirmed_sha`, so a resolution with no new commit can be tagged
  `resolved_by_commit`.
- **Continual runs cannot edit the prompt they refine.**
  `build_continual_run_configurable` (`agent/review/style_jobs.py:61`) passes only
  the repo and mode, and `_prepare` (`agent/analyzer.py:110`) injects only sample
  text, never `ReviewStyle.custom_prompt` (`agent/review/styles.py:49`). The
  `continual-learning` skill assumes the current prompt is available.

## Proposal

### One outcome per terminal transition

Every transition of a published finding to a terminal status (`resolved`,
`dismissed`) emits exactly one outcome, from any actor: agent tools, webhook
reconciliation, or reaction. Provenance stays in `label_source` and metadata
(actor login, source, active reactions) — never in example identity.

### Per-finding identity

Examples are keyed by `(repo, finding_id)`. A finding has one current label; the
latest terminal transition wins. `read_outcomes_for_repo` deduplicates by
`finding_id` and returns it, plus `side` and start/end lines so the analyzer can
quote concrete context.

### SHA semantics

Record `first_seen_sha` as published and the head SHA observed at the transition.
Classify `resolved_by_commit` only when a commit landed after first sighting; a
resolution with no code change stays `resolved_same_sha`. Reaction retraction
removes the outcome rather than leaving a stale label.

### Human resolutions

`reconcile_findings_with_review_threads` emits on the open→resolved transition
when given repo, PR, head SHA, and thread context, regardless of caller. Existing
callers (`agent/github/webhook.py`, `agent/reviewer.py`,
`agent/tools/publish_review.py`, `agent/tools/resolve_finding_thread.py`) pass the
context once.

### Reactions

Add `reaction` to `SUPPORTED_GH_EVENTS` and route `created`/`deleted` to the
existing handlers after the allowlist and public-repo org gate. Bot senders are
ignored. Missing findings and lookup failures are logged and swallowed so a
webhook is never failed by a stale reaction.

### Continual prompt context

Continual runs receive the stored `custom_prompt` and `analysis_summary` in their
context so they refine the current prompt instead of re-deriving it. Bootstrap is
unchanged.

### Non-goals

- Changing the outcomes dataset, tenant, or example schema beyond identity and
  dedup.
- New reaction content types beyond 👍/👎.
- Applying refined prompts to the reviewer without the existing review and eval
  gating.
- Replacing the analyzer with a separate model-training pipeline.

## Security and privacy

No change to trust boundaries. Reaction webhooks are untrusted input and pass the
same repository allowlist and public-repo org gate as every other event; bot
senders are skipped and failures are logged rather than surfaced. Attribution
stores GitHub logins that are already recorded today, and outcomes continue to
land in the existing LangSmith dataset.

## Alternatives

- **Keep per-`label_source` example identity:** preserves history, but trains on
  contradictions when a finding flips labels and cannot represent a retraction
  without a second row. Per-finding identity with latest-wins matches the stated
  intent of one current label per finding.
- **Store outcomes in the application database:** duplicates the dataset the
  analyzer and `read_finding_outcomes` already read, and adds migration and
  retention work for no new capability.
- **Poll reactions through the GitHub API instead of webhooks:** adds rate-limit
  pressure and latency for events GitHub already delivers.

## Unresolved questions

- On reaction removal after an outcome exists, should the example be deleted, or
  should a neutral marker be written?
- Is an explicit "won't fix" dismissal meaningfully different from a thumbs-down
  false positive, or should both map to `dismissed`?
- Do we migrate existing per-`label_source` examples, or let new transitions
  overwrite them as they occur?
