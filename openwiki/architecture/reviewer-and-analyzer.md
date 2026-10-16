---
type: architecture
title: Reviewer Graph and Code Review
description: Architecture of the isolated reviewer deep-agent graph, including repository preparation, durable finding reconciliation and publication, input safety, and differential code review.
tags: [reviewer, code-review, findings, langgraph, sandbox, github, deep-agent]
sources:
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-90b15fd6117126ebfe5b6b22
    resource: repo://openswe/graphs/reviewer.py
  - id: openwiki-source-85f325a37c97d6000b6e6a23
    resource: repo://openswe/review/findings.py
  - id: openwiki-source-77c518a8a4572e59e2d374ee
    resource: repo://openswe/review/publish.py
  - id: openwiki-source-f47abef99c7b9c6cdca43421
    resource: repo://openswe/review/reconcile.py
  - id: openwiki-source-eb9e695bef31bca8f88b0c2b
    resource: repo://openswe/review/styles.py
  - id: openwiki-source-96bcad07b4fe7078402bc2b8
    resource: repo://openswe/reviewer.py
  - id: openwiki-source-96907ca866d8ca4c8369bc1b
    resource: repo://openswe/tools/add_finding.py
  - id: openwiki-source-7cec199cafafc864b85fba49
    resource: repo://openswe/tools/publish_review.py
  - id: openwiki-source-065c69ba95cc740a2282dd3c
    resource: repo://tests/reviewer/test_factory_config_isolation.py
generated: { by: "openwiki/0.4.2", at: "2026-10-13T15:44:17.388Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-10-13T15:44:17.388Z
---

# Reviewer Graph and Code Review

Open SWE exposes one specialized deep-agent graph: `reviewer` (`openswe.graphs.reviewer:traced_reviewer_agent`). The reviewer evaluates a GitHub pull request using durable, per-PR findings stored in LangGraph thread metadata. It reviews code against repository style, organizational guidelines, API standards, and approval policies.

For trigger routing and webhook behavior, see [PR Review Workflow](../workflows/pr-review.md). For sandbox provider and recovery behavior, see [Sandbox Lifecycle](sandbox-lifecycle.md), and for the broader tool catalog, see [Tools](../concepts/tools.md).

## Reviewer: constrained PR assessment

### Authority boundary and construction

The reviewer is read-only with respect to the repository. Its system prompt prohibits commits, pushes, and direct `gh pr review` or review-API calls. It has no coding, commit, push, or PR-opening tools: repository changes cannot be an agent action. GitHub review mutation is centralized in `publish_review` and the finding-thread tools.

`get_reviewer_agent(config)` creates the graph per run. It shallow-copies the outer config and its `configurable` mapping before supplying a default recursion limit, preserving the caller's configuration. If the run has no `thread_id`, or the graph is not loaded for execution, it deliberately returns an empty deep agent rather than provisioning a sandbox.

For an executable run, the factory selects reviewer and subagent models from explicit configuration or team defaults, applies the Fable model gate, and attaches a cached sandbox backend with a reconnect closure. Its explicit tools are:

- review lifecycle: `fetch_review_diff`, `add_finding`, `update_finding`, `list_findings`, `publish_review`, `resolve_finding_thread`, and `reply_to_finding_thread`;
- read-only external helpers: `web_search`, `fetch_url`, and `http_request`.

It permits one `reviewer` subagent. The parent assigns a disjoint file partition; the subagent returns only candidate defects and has neither finding nor publication tools. The parent remains responsible for validation, persistence, and publication.

### Run preparation, GitHub access, and context

`PrepareReviewerRunMiddleware` performs deterministic setup before the first model call. For a configured source repository it mints a repository-scoped GitHub App installation token, caches it as the thread's bot token, and supplies it to the sandbox GitHub proxy. It then ensures a sandbox with `allow_replacement=True`, clones or fetches the repository, force-checks out the PR head, and materializes trusted repository skills from the base revision.

The middleware computes the review range and its unified diff, including delta-only re-review ranges, then derives the changed `(file, side, line)` set. It puts `diff_text` and `diff_line_set` in run state. This lets `add_finding` reject invalid anchors at creation time rather than waiting for GitHub to reject a batch.

In parallel, preparation fetches PR title and body, existing GitHub review threads, saved repository custom style prompt, organization guidelines, root and scoped `AGENTS.md`/`CLAUDE.md` from the base SHA, an API standards skill, and optional review scout walkthrough. Existing threads are reconciled before their prompt block is rendered. Once the diff is available, scoped instructions are selected for changed files. The rendered prompt then selects first-review, re-review, or finding-reply guidance.

```mermaid
flowchart TD
    Trigger["Reviewer run"] --> Prep["PrepareReviewerRunMiddleware"]
    Prep --> Auth["App token and sandbox proxy"]
    Prep --> Checkout["Checkout PR head and trusted skills"]
    Prep --> Diff["Review diff and changed-line set"]
    Prep --> Context["PR, threads, guidance, conventions"]
    Context --> Reconcile["Reconcile GitHub review threads"]
    Diff --> Agent["Reviewer agent"]
    Reconcile --> Agent
    Agent --> Finding["add_finding or update_finding"]
    Finding --> Publish["publish_review"]
    Publish --> GitHub["PR review and thread resolution"]
    Publish --> Metadata["Findings and reviewed SHA"]
```

Reviewer setup and publication flow. Context retrieval is concurrent during preparation; reconciliation runs while loading the existing-thread context.

If sandbox replacement itself fails with `SandboxUnreachableError`, preparation posts a typed unreachable-sandbox notification on the PR and fails the run instead of silently leaving it unreviewed. This replacement policy is safe because the checkout is re-derived each run and findings are not sandbox state.

### Prompt and input-safety constraints

The prompt requires a concrete, changed-line-anchored failure mode and rejects speculation, ordinary style or naming nits, pre-existing defects, and duplicate fan-out for one defect across files. Explicit repository convention violations remain reviewable when they are anchored in the diff and have a concrete failure mode. Suggestions are restricted to small, obvious fixes.

PR title/body, existing review-thread comments, and finding replies are attacker-controlled GitHub content. The renderer places them in XML data blocks, treats their contents as data rather than instructions, validates login attributes against the GitHub login grammar, and neutralizes wrapper closing tags with `_escape_for_data_block`. Review scout walkthrough and human input are also explicitly untrusted and must not leak into actual findings.

### Durable finding lifecycle

Findings are stored in LangGraph metadata on the deterministic reviewer thread, rather than in the sandbox. `reviewer_thread_id(owner, repo, pr_number)` uses UUID5, allowing webhooks, dashboard code, and runs to retrieve the same one-per-PR thread across pushes. `set_reviewer_thread_metadata` writes `kind: "reviewer"`; that tag supports cross-thread lookup for the UI and usage aggregation.

A `Finding` records its location and side, severity and confidence, title/description/suggestion, diff membership and hunk, status, first and last-confirmed SHAs, publication identities, surface state, human-reply/reconciliation fields, fingerprint, and interaction history. Legacy persisted shapes are normalized on read. Surface state is monotonic: normalization resolves contradictory legacy data by retaining the furthest state.

```mermaid
stateDiagram-v2
    [*] --> NotSurfaced: add in-diff finding
    NotSurfaced --> Surfaced: GitHub inline comment posted
    Surfaced --> ResolvePending: finding resolved or dismissed
    ResolvePending --> ResolvedSurface: GitHub thread resolved
    ResolvedSurface --> [*]
```

Surface state moves forward independently of the finding's `open`, `resolved`, or `dismissed` status.

`add_finding` validates title, severity, confidence, side, and ordered line range. It resolves diff context from injected run state first, then `configurable`, then a fresh authenticated PR diff. A line-anchored range absent from the relevant diff side returns `success: false` and `in_diff: false`; the prompt tells the model not to re-anchor or retry. File-level findings are accepted but do not render inline. Successful findings retain an extracted diff hunk when diff text is available, clip suggestions beyond four lines, and deduplicate through their content fingerprint.

Before every normal run, `reconcile_findings_with_review_threads` matches findings to GitHub threads first by embedded marker, then recorded thread or comment identity. It backfills comment/thread IDs and marks matched findings surfaced. A finding becomes resolved only when all matched threads are resolved; outdated-but-not-resolved threads do not resolve it. The latest non-bot reply after the bot comment is retained as a `human_reply` interaction with `needs_reassessment`, giving a subsequent re-review a durable reason to reconsider.

### Publication and failure semantics

`publish_review` filters unpublished, in-diff, open findings at or above its severity threshold (default `medium`) and caps the batch at `REVIEW_FINDING_CAP` (6). A call posts one GitHub PR Review containing a fixed host-generated summary and one inline comment per renderable finding; a suggestion becomes a fenced `suggestion` block. Each comment includes an `open-swe-review-comment` JSON marker with finding identity and anchor metadata, which supports reconciliation and recovery of lost IDs.

On successful publication, the tool records review/comment/thread identities, resolves threads for resolved findings through GraphQL `resolveReviewThread`, advances `last_reviewed_sha`, records usage, clears the started-review comment, and settles the GitHub review check. On re-review, already published findings are not posted again, and a run with no new inline comments can intentionally skip a duplicate empty review while still resolving threads and advancing state.

Callers must inspect the structured result: `success: true` with `review_id: null` and `skipped_empty_re_review: true` is a valid no-post outcome; `dry_run: true` is evaluation simulation. A numeric `review_id` confirms a real review. If GitHub reports an unresolved anchor, the tool filters invalid findings and retries once with valid anchors when possible; otherwise it returns `unresolvable_findings` and a remediation hint, avoiding blind retries. Missing durable thread state is similarly a structured do-not-retry result.

## Review style customization

Repositories can define a custom review style prompt through the dashboard. This prompt supplements the reviewer's main system prompt during preparation when `get_repo_custom_prompt` retrieves it from the `REVIEW_STYLES` store. The store lookup fails soft: a store blip omits style guidance rather than failing a PR review.

The `ReviewStyle` record holds a `custom_prompt`, approval mode (off/dry_run/approve), and audit timestamps. Callers update the prompt through `ReviewStyleStore.update_prompts`, which handles validation and UI topic invalidation.

The analyzer graph that once synthesized these custom prompts per-repository has been removed. Custom prompts are now authored in the dashboard and persisted directly.

## Focused tests

The reviewer suite covers config isolation, diff and tool validation (including LEFT-side anchors), durable finding behavior, publishing and marker rendering, reconciliation, trace context, trigger/watch behavior, and review API/chat paths. In particular, `test_factory_config_isolation.py` protects the reviewer config-copy invariant; `test_reviewer_tools.py` exercises validation and persistence decisions; `test_reviewer_reconcile.py` covers marker backfill and terminal-thread rules; and `test_reviewer_publish.py` covers rendered markers and suggestions.
