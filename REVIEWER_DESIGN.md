# Reviewer Agent — Design

Design for the Open SWE Reviewer Agent. Goal is to match Devin Review's *perceived* quality (won internal A/B vs Graphite) on PR review, with a single evolving findings list and a "watch" mode that re-reviews on new commits. Eval plan is tracked separately in `REVIEWER_EVAL_PLAN.md`.

## Goals

- A reviewer agent that produces high-signal findings on a PR, surfaced as **inline GitHub review comments** at the relevant file/line range, with **```suggestion blocks** (the "Commit suggestion" UX) where the agent can offer a concrete fix.
- Filter what's surfaced to GitHub by severity — a clean PR shouldn't have five filler comments appended to one critical one.
- A single evolving findings list per PR — resolved findings are kept (status=resolved), not pruned.
- A "watch" mode that re-reviews on new commits to a previously reviewed PR, reconciling existing findings (resolved / still-open / updated), adding new ones, and resolving GitHub review threads for findings that the new commits addressed.
- One canonical reviewer thread per PR, regardless of how the review was triggered.
- Headless first; eventually a UI for the full findings list.

## Non-goals (for now)

- Multi-pass / multi-agent review orchestration. One reviewer agent, one thread, one evolving list.
- Deterministic resolution-detection logic. Reconciliation is LLM reasoning over (previous findings, new diff, sandbox state).

## Dispatch model

**One canonical reviewer thread per PR.** Thread id is derived deterministically from the PR URL (or `owner/repo#number`), the same way the existing webhook handlers in `agent/utils/` already derive thread ids per source. Every trigger surface routes to the same thread, so findings, sandbox, and watch state stay continuous.

Trigger surfaces:

1. **Slack** — handled by the main agent. The user asks the bot to review a PR; the main agent calls a `request_pr_review(pr_url, message)` tool. That tool resolves the canonical reviewer thread id from the PR URL and triggers a run via `langgraph_sdk` (same pattern as the existing GitHub PR webhook path). This already exists in some form (see `agent/tools/request_pr_review.py`).
2. **GitHub webhook — review requested.** Direct invocation of the reviewer graph against the canonical thread.
3. **GitHub webhook — push to a watched PR.** Direct invocation against the same canonical thread; the watch flag in thread metadata gates whether we trigger.

This unifies all paths: the reviewer graph never has to ask "who triggered me?" — it always sees the same thread and the same state, with a structured user message describing what to do.

## Findings as first-class state

Findings live in **LangGraph thread state**, not in sandbox files. Sandboxes are evictable; thread state survives.

```python
class Finding(TypedDict):
    id: str                       # stable, e.g. "f_<short-uuid>"
    severity: Literal["informational", "low", "medium", "high", "critical"]
    category: str                 # e.g. "correctness", "security", "perf", "style", "flag"
    file: str
    start_line: int | None        # None when the finding is file-level
    end_line: int | None          # equals start_line for single-line findings; >start_line for ranges
    side: Literal["LEFT", "RIGHT"] # LEFT = base/old, RIGHT = head/new — almost always RIGHT
    description: str              # the body the user sees
    suggestion: str | None        # if set, rendered as a ```suggestion block — gives the user a "Commit suggestion" button on GitHub. Must replace exactly start_line..end_line.
    status: Literal["open", "resolved", "dismissed"]
    first_seen_sha: str           # SHA at which this finding was first introduced
    last_confirmed_sha: str       # most recent SHA where this finding was still open
    github_review_comment_id: int | None  # populated after publish; used to resolve the thread on re-review when status moves to resolved
    diff_hunk: str | None         # snippet of the relevant diff cached at finding-creation time, so the future UI / Slack / Linear renderers can render the diff alongside the finding without re-fetching from GitHub or the sandbox (which is evictable)
```

**Storage: thread metadata.** Findings live in LangGraph thread metadata under the reviewer thread, queried via the langgraph SDK client. Same pattern as existing thread metadata (`sandbox_id`, `github_token_encrypted`). Avoids fighting deepagents' state abstraction. The future UI lists "PRs being reviewed" by querying threads filtered on `metadata.kind == "reviewer"`, and reads each thread's findings list directly.

Reviewer-thread metadata schema:

- `kind: "reviewer"` — sentinel so the UI / SDK queries can filter to reviewer threads
- `pr: {owner, name, number, url, title, head_ref, base_ref}` — PR identity, what the UI displays in the list
- `findings: list[Finding]`
- `last_reviewed_sha: str | None`
- `watch: bool` (whether push events should re-trigger)
- (existing) `sandbox_id`, `github_token_encrypted`

Tools the reviewer agent uses to mutate findings:

- `add_finding(severity, category, file, title, description, start_line, end_line, suggestion=None, side="RIGHT") -> id`
  - `title` is a concise generated headline for the GitHub comment, not a copied/truncated description.
  - Single-line finding: `start_line == end_line`.
  - File-level finding: both `None` (publishes as a top-level review body line, not inline).
  - `suggestion`, when set, must be the exact replacement text for lines `start_line..end_line` inclusive — that's how GitHub's ```suggestion block works.
- `update_finding(id, *, status?, severity?, title?, description?, suggestion?, note?)` — single tool for any post-creation mutation, including marking resolved/dismissed and revising a suggestion after the agent looks more carefully.
- `list_findings(status_filter?) -> list[Finding]`

Resolved findings are kept in the list (status=`resolved`), hidden from the default top-K GitHub surfacing, surfaced in the eventual UI as "what's already addressed". This prevents the agent from re-finding the same issue across runs.

## Publishing to GitHub (decoupled from finding production)

The agent's job is to produce findings. A separate step publishes them to GitHub as a Review with inline comments.

**Surfacing format: GitHub Pull Request Review.** Single API call (`POST /repos/{owner}/{repo}/pulls/{n}/reviews`) creates one review with:
- a top-level **review body** — agent-authored summary / overall take on the PR
- an array of **inline comments**, one per surfaced finding, anchored to `path` + `line` (+ `start_line` for ranges) + `side`
- `event: "COMMENT"` (not `REQUEST_CHANGES` — we don't want the reviewer agent to gate merges)

Findings with a `suggestion` get the suggestion appended to the comment body as a ```suggestion fenced block, which gives the user GitHub's native **"Commit suggestion"** / **"Add suggestion to batch"** UX. For multi-line ranges, a multi-line ```suggestion block replaces the entire range.

Severity ladder (matching Devin Review): `informational` < `low` < `medium` < `high` < `critical`. `informational` is for purely contextual / FYI observations (e.g. "this codebase uses pattern X elsewhere") — not flaws. It still supports suggestions; the agent can use it for stylistic nudges that aren't actually wrong.

Severity-threshold filter (not pure top-K): publish all findings with severity ≥ `medium` by default, with a hard cap of 4 to avoid review spam. Pure top-K means a clean PR with one critical issue gets four filler findings appended — bad UX. `informational` and `low` findings are produced into state and visible in the eventual UI / full list, but not surfaced to the GitHub PR by default.

**Mechanism: a `publish_review` tool the agent calls deliberately** at the end of its run. Why a tool, not after-agent middleware:

- Clearer in traces — explicit step in LangSmith.
- The agent can choose to skip publishing on a re-review run where nothing changed surface-worthy.
- Failure modes (line not in diff, suggestion conflict, GitHub 422) surface back to the agent, which can adjust and retry.
- We don't have to bake the severity-threshold policy into middleware; the agent can override (e.g. publish a `low`-severity nit on a tiny PR if it's the only finding).

Decoupling finding production from publishing is what lets us swap the GitHub Review surfacing for a richer UI later without touching the agent loop.

### Inline comment constraints (and how to handle them)

GitHub's inline review comments require the line to be **part of the PR diff** — meaning `line` (and `start_line..line` for ranges) must fall within an actual hunk in the PR's diff against its base. Implications:

- **Findings on lines the PR didn't touch** (e.g. agent notices a pre-existing bug while reviewing context) cannot be inline comments. Two options:
  1. Drop them from the review (agent should be discouraged from reporting these in the first place — system prompt says "review the diff, not the surrounding code").
  2. Append them to the **review body** as a "Pre-existing observations" section.
  Default to (1); allow (2) only when severity is `high`/`critical`.
- **Suggestions only apply to lines in the diff**, since GitHub applies them as a follow-up commit on the PR branch. Suggestions on out-of-diff lines are a hard error from the GitHub API.
- **The reviewer's prep node should compute the set of (file, line) tuples in the diff** and pass it to the agent as part of the review context. The `add_finding` tool can validate against that set and reject (or auto-flag) findings that fall outside it, rather than failing at publish time.

### Re-review and resolved threads

On a re-review run, when a finding moves from `open` → `resolved`, the reviewer should also **resolve the corresponding GitHub review comment thread** (via the GraphQL `resolveReviewThread` mutation, since REST doesn't expose this). That's why `Finding.github_review_comment_id` is stored — without it, we can't reconcile back to the thread on GitHub.

For findings that move from `open` → `open-but-updated` (e.g. the agent revises severity or suggestion based on new context), the simplest behavior is to leave the existing GitHub thread alone and let it stand; richer behavior (post a follow-up reply in that thread) is a follow-up.

## Cold-start / warm-path entry contract

Sandbox handling was reworked recently (#1249): `ensure_sandbox_for_thread` now does cache → ping → start-if-idle → refresh proxy → recreate only on hard failure. So warm-path is realistic between an initial review and a follow-up push, especially for tight feedback loops where the gap is short.

**Deterministic prep node before the agent's first model call:**

```
if /workspace/<repo> exists:
    git fetch && git checkout <target_sha>
else:
    gh repo clone <owner>/<repo> /workspace/<repo> && git checkout <target_sha>
```

Either branch produces the same entry contract for the agent:

> "You are in `/workspace/<repo>` checked out to `<sha>`. Existing findings: [...]. Diff since `<last_reviewed_sha>`: [...]. Your job is to: ..."

Why a deterministic prep node and not a prompted agent step:

- No tokens burned on "okay, cloning now..." narration.
- Auth / network / missing-branch failures surface as discrete graph errors instead of the agent thrashing through tool retries.
- Discrete step in the LangSmith trace, not buried inside agent tool calls.
- Matches the existing pattern where sandbox creation + GitHub proxy config already live outside the agent in `get_agent`.

The agent can still pull additional context (full PR diff, base branch, related files) via the standard tools when it decides it needs to.

## Re-review prompt context

The structured user message that triggers a re-review run on a watched PR's new commit:

```
A new commit has been pushed to the PR you previously reviewed.

PR: <owner>/<repo>#<number>
Previous reviewed SHA: <last_reviewed_sha>
New HEAD SHA: <new_sha>

Existing findings:
- [<id>] (<severity>, <category>) <file>:<line> — <description>  [status: <status>]
- ...

Diff since previous reviewed SHA:
<diff>

For each existing open finding, decide whether the new commits:
  - resolved it (mark resolved with update_finding(status="resolved"))
  - left it unchanged (no action)
  - changed it materially (update via update_finding with a note, or update + revised suggestion, or close + add_finding)

Then review the new diff for any net-new issues and add them with add_finding.
Finally call publish_review to post inline comments + suggestions for the new findings, and resolve the GitHub threads for findings that just moved to resolved.
```

Diff scope is the diff *since `last_reviewed_sha`*, not the full base...head PR diff. That's what resolution detection actually needs. The agent can fetch the full diff on demand if it wants broader context.

First-review just has empty findings list and a full base...head diff.

## Watch mode

- `watch: bool` flag on the reviewer thread, set to `True` implicitly on first successful review.
- New webhook handler for GitHub `push` events: if the push is to a PR's head ref and that PR's reviewer thread has `watch=True`, emit the structured re-review user message into the thread (via `langgraph_sdk`, same dispatch path as other webhook triggers).
- An explicit `unwatch_pr` tool / API endpoint stops watching (e.g. once the PR is merged or the user is done). Closing/merging a PR can auto-unwatch via the existing `pull_request` webhook.

Edge cases worth thinking about (not blockers):

- **Empty diff since last SHA** (rebase, force-push that resolves to same tree) — skip the run at the webhook level, don't trigger the agent.
- **Force-push that drops `last_reviewed_sha` from history** — `git fetch` will lose the old SHA's reachability. The diff scope falls back to base...head; treat as a fresh review of the new state, but keep existing findings as starting context for reconciliation.
- **Long gap between review and push** — sandbox evicted, falls into cold-start path automatically via the existing recreation logic. No special handling needed.

## Graph shape (sketch)

```
reviewer_graph:
    prep_sandbox             # ensure_sandbox_for_thread (existing)
    prep_repo                # NEW: clone-or-fetch + checkout target_sha
    build_review_context     # NEW: assemble findings + diff + diff-line-set into user message (or branch on first-review vs re-review)
    agent                    # create_deep_agent with reviewer-specific prompt + finding tools
    # (publish_review is a tool the agent calls; not a node)
```

`prep_sandbox`, `prep_repo`, and `build_review_context` are all deterministic graph nodes, not agent tool calls. `build_review_context` reads thread state and the diff, computes the set of (file, line) tuples that are part of the PR diff (so `add_finding` can validate against it), and produces the user message that seeds the agent run.

## Open questions

1. **Severity threshold for default surfacing.** Default to `medium`+? `high`+? Worth tuning against the eval set. Independent of the cap (currently 4). `informational` is always below the threshold by design — it's a UI-only tier.
2. **Pre-existing-bug findings.** Drop entirely from the review (clean), or surface in the review body for `high`/`critical` only (more complete)? Currently leaning drop, with a system prompt instruction telling the agent not to report them in the first place.
3. **Findings dedup across runs.** When the agent calls `add_finding` on a re-review, do we trust it not to duplicate, or do we do server-side dedup based on (file, line, category, description-similarity)? Probably trust the agent for now (it sees existing findings in the prompt) and add server-side dedup only if we observe duplicates in eval.
4. **Updated-but-not-resolved findings on re-review.** When a finding stays open but the agent revises its suggestion (e.g. new commits made the original fix obsolete but the issue stands), do we leave the existing GitHub thread alone, post a follow-up reply, or close + repost? Leaning leave-alone for v1.
5. **Reviewer system prompt.** Needs to bake in: single evolving findings list, prefer updating existing findings over adding new ones, only surface in-diff findings, write actionable suggestions where possible, severity calibration matches Devin Review. Highest-leverage piece for matching Devin's perceived quality — iterate against the eval set.

## Implementation order

1. **Findings state + tools.** Add `Finding` schema (with `start_line`/`end_line`/`suggestion`/`side`), `add_finding` / `update_finding` / `list_findings` tools, thread-state extensions. No watch, no publish — just the agent producing a findings list visible in state.
2. **Prep nodes.** `prep_repo` (clone-or-fetch + checkout) and `build_review_context` (computes diff + diff-line-set, branches on first-review vs re-review) as deterministic graph nodes before the agent.
3. **`publish_review` tool.** Posts a GitHub PR Review with body + inline comments + ```suggestion blocks. Stores `github_review_comment_id` back on each Finding for later reconciliation.
4. **Reviewer system prompt iteration.** Calibrate against the eval set in `REVIEWER_EVAL_PLAN.md` — severity calibration, in-diff-only discipline, suggestion quality.
5. **Watch mode.** `watch` flag, push webhook handler, re-review user message format, `unwatch_pr` on PR close/merge, GraphQL `resolveReviewThread` on findings that move to resolved.
6. **(Future)** UI for full findings list, server-side dedup if needed, follow-up replies in existing threads when findings are revised.

## Followups / notes

- **Protected-attribute access in `_start_langsmith_sandbox_if_needed`** (`agent/server.py:76-77`) reaches into `sandbox._sandbox._client.get_sandbox_status`. If those internals shift upstream, the warm path silently degrades to always-recreate. Worth either pinning the assumption with a comment or a focused test against a mocked LangSmith client that exercises the start-if-idle branch specifically. Not urgent.
