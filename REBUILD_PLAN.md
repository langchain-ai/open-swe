# Open SWE Agent — Rewrite Plan

Status: spec for a **single PR**. The implementing agent builds the entire rebuild in **one PR** and marks it ready for review. (The live Slack/sandbox/LangSmith path can't be exercised from the dev box, so "ready for review" means it compiles and `make lint` + `make test` pass; the finish-rate check in §7 is a post-merge canary the team runs.) Scope: **focused core rebuild** — dispatch + agent core + prompt + sandbox + tools. Keep dashboard/reviewer/analyzer as-is.

**Decisions (locked):**
- Focused core rebuild (not clean-room).
- Adopt the already-installed deepagents `skills=`/`memory=`/profiles. **Do not bump deepagents** — the installed 0.6.8 already has every feature used here; the 0.6.12 bump is unrelated maintenance and is out of scope (langchain-1.x peer-dep risk).
- Follow-up UX = `multitask_strategy="interrupt"` + webhook-side debounce; **delete the custom store-queue.**
- **ci_autofix / PR-babysitting removed** (it isn't working; delete `ci_autofix.py` + retire the `ci_monitor` graph).

---

## 1. TL;DR

- **The regression is real:** PR #1251 (`bb836b58`) dropped the platform-level `multitask_strategy` backstop, leaving only a custom store-queue gated by an unreliable `thread.status=="busy"` check. The correct fix (busy-check via `runs.list`) is **unmerged** on `handle-hanging-threads-better`.
- **~36% of Slack runs never finish** (570 runs/4d, ~60% reply). Two causes: (A) follow-ups spawn duplicate/stranded runs instead of injecting; (B) long runs die on server recycle (`Lifespan cancelled`) and lose all work — no checkpoint-idempotency, no run budget, no failure reply.
- **The fix is mostly using what you already have.** The platform gives durable checkpoint/resume, `multitask_strategy`, and completion webhooks. Installed deepagents 0.6.8 already ships `skills=`, `memory=`, harness profiles, and tool-result offloading — the main agent just doesn't use them and stacks a ~7.5k-token prompt on top of deepagents' 2,258-char base prompt.
- **Rebuild = rewire, not migrate.** A thin durable dispatch layer + one slim deepagents-leveraged agent + a ~2k-token prompt + all-async tools.

---

## 2. Evidence

- Finish rate ~60%; pending-orphaned >30 min = **36% (206/570)**. Validated behaviorally: 2/20 stalled runs replied vs 14/15 successful.
- Stalled split: 27% never made a model call, 18% died early, 55% long-then-died. Max stalled run **25.2M tokens**.
- `error` runs ≈ all `CancelledError`, incl. `Task group Lifespan cancelled` (server recycle), on 2.5M–25M-token runs.
- Snowball: 93% of stalls share a thread with another; up to **13 stalled runs on one thread**. Median finished run 3.5 min.
- Repro thread `ad2c63c1`: run1 = 89 model calls / 113 tool calls over 18 min → silent at the 90th call → never replied; follow-up pings died at startup; only run4 replied.

---

## 3. Root causes → fixes

| Cause | Fix |
| --- | --- |
| A. Follow-ups become duplicate/stranded runs; `thread.status` busy-check unreliable; store-queue drains only on next model call; in-process Slack-only lock | §5.2: always `multitask_strategy="interrupt"` + debounce; delete is_thread_active + store-queue + `_THREAD_RUN_LOCKS` |
| B. Runs die on recycle, lose work, post nothing; default `durability="async"`; non-idempotent side effects | §5.2: `durability="sync"` + idempotent side effects + completion-webhook failure reply + reconciliation cron |
| C. No run budget (cap 5000 / recursion 9,999); 25M-token runs | §5.6: budget + graceful degradation (commit WIP + reply on limit) |
| D. Restart loops → pending orphaning; 29 `asyncio.run` sync tools; no HTTP timeouts | §5.3 all-async tools; §5.2 shared timeout client |
| E. ~13.5k-token prompt (ours ~7.5k stacked on deepagents base ~2.3k); 6× duplication; repo setup in prose | §5.4 harness-profile prompt replacement, move workflow to code |
| F. Context bloat (129k first call) | §5.5 deepagents tool-result eviction + summarization (already installed) |
| G. Complexity: webapp.py 3601 LOC, 3 sandbox recreate paths, model resolution twice | §5.1 layout; §5.3 collapse |

---

## 4. Design principles

1. **Lean on the platform** for durability, concurrency, and completion signals.
2. **Lean on deepagents** for tools, planning, summarization, eviction, tool-call repair, skills, memory.
3. **Determinism in code, judgment in the prompt** — mechanical steps (clone, git identity, branch, AGENTS.md, dep-firewall) run in code.
4. **Every run ends with a signal** — success, failure, or timeout; the user always gets a reply, even if the agent died.
5. **Idempotent side effects** — a checkpoint replay must not double-commit or double-post.

---

## 5. Target architecture

### 5.1 Module layout
```
agent/
  dispatch.py        # SINGLE dispatch contract — replaces the ad-hoc runs.create sites
  webhooks/
    slack.py linear.py github.py   # thin: parse → thread_id → dispatch(); split out of webapp.py
  completion.py      # completion-webhook handler: failure/timeout replies
  reconcile.py       # reconciliation sweep (runs in the existing `scheduler` graph)
  agent_core.py      # get_agent: unified model resolution + create_deep_agent assembly
  prompt.py          # slim prompt, installed as a harness-profile base_system_prompt
  sandbox/           # one get-or-create, one recreate path, exposed as a BackendFactory
  tools/             # all async; zero asyncio.run
```
Keep `dashboard/`, `reviewer.py`, `analyzer.py`, `chat.py`. **Remove** `ci_autofix.py` and retire the `ci_monitor` graph (PR-babysitting dropped).

### 5.2 Dispatch contract (the core fix) — `dispatch.py`
One function behind every trigger:
```python
async def dispatch_agent_run(thread_id, content_blocks, configurable, *, source):
    await client.runs.create(
        thread_id, "agent",
        input={"messages": [{"role": "user", "content": content_blocks}]},
        config={"configurable": configurable, "recursion_limit": RUN_RECURSION_LIMIT},
        multitask_strategy="interrupt",     # follow-up halts + resumes WITH the new message
        durability="sync",                  # checkpoint before each step → survive recycle
        webhook=COMPLETION_WEBHOOK_URL,     # platform calls us on completion/failure
        if_not_exists="create",
    )
```
- **Delete `is_thread_active` + the store-queue + `check_message_queue_before_model` + `_THREAD_RUN_LOCKS`.** `multitask_strategy="interrupt"` is the platform-native, cross-process way to inject a follow-up into the active run: it halts the current run (progress preserved in the sync checkpoint) and resumes the agent with full history + the new message. On an idle thread it just runs. Robust where the busy-check + store-queue were racy.
- **Debounce** rapid Slack follow-ups/edits in the webhook (~2–3 s) so a burst batches into one resume instead of thrashing interrupts.
- **Half-done tool call** on interrupt is handled by the built-in `PatchToolCallsMiddleware` (repairs the orphaned call on resume) + sync checkpointing; the debounce shrinks the window. Acceptable trade for robust delivery.
- **`durability="sync"`**: a crash/recycle resumes from the last checkpoint instead of losing work (default `async` risks "no checkpoint on crash").
- **Completion webhook** (`completion.py`): platform POSTs `{status, error, values, run_ended_at}`; handler posts a failure/timeout reply to the source channel iff the run failed/timed out or ended without a recorded reply. Decouples "user gets an answer" from "agent remembered to reply." Set `webhooks.url.disable_loopback: false` in `langgraph.json` (same-server route).
- **Reconciliation** (`reconcile.py`, in `scheduler`): periodic `runs.list(status="pending")` for stale runs → re-trigger or post failure; `runs.cancel_many(...)` for backlogs.
- **Timeouts**: a shared `httpx.AsyncClient(timeout=...)`; ban bare `httpx.AsyncClient()` (~15 untimed sites today).
- Route **all** triggers (Slack/Linear/GitHub/issue) through this one function.

### 5.3 Agent assembly — `agent_core.py`
- `create_deep_agent` with curated tools, sandbox `BackendFactory`, harness-profile prompt (§5.4), `skills=` (reuse `analyzer_skills.py` pattern), `memory=["/memory/AGENTS.md"]`.
- **Delete / replace with built-ins:** `repair_orphaned_tool_calls` → `PatchToolCallsMiddleware` (diff first); custom `model_fallback` → upstream `ModelFallbackMiddleware`; `exclude_tools` → profile `excluded_tools`; redundant general-purpose subagent (auto-added); custom context handling → built-in summarization + eviction (§5.5).
- **Keep:** sandbox lifecycle + GitHub proxy (as `BackendFactory`), integration middleware (Slack status, plan mode, circuit breaker, sanitizers), `ModelCallLimitMiddleware` (lower cap, §5.6).
- **All tools async** — rewrite the 29 `asyncio.run` tool bodies; `requests`→`httpx`; wrap unavoidable sync in `asyncio.to_thread`.
- **Unify model resolution** into one function; fix the stale fallback target (`model.py:74` → `claude-opus-4-5`, not in supported set).
- **Collapse the sandbox lifecycle** to one recreate path (today 3–4 overlap).

### 5.4 Prompt redesign — `prompt.py` (~2–2.5k tokens)
- **Own the whole prompt via a harness profile** (no stacked voices):
```python
from deepagents.profiles.harness.harness_profiles import HarnessProfile, register_harness_profile
register_harness_profile("anthropic", HarnessProfile(base_system_prompt=OPEN_SWE_PROMPT, excluded_tools={...}))
create_deep_agent(..., system_prompt=None)   # CUSTOM replaces deepagents BASE
```
- **Move mechanics into code:** repo setup (clone, git identity, branch) → deterministic pre-run step reusing the proven `repo_prep.py` pattern (reviewer already pre-clones); AGENTS.md → `memory=`; `sfw` firewall → sandbox hook; PR templates → `open_pull_request` defaults.
- **De-duplicate** (PR/commit ×6→1, "never run full suite" ×3→1, Slack mrkdwn → tool only, "never claim PR" ×3→1); **delete ALL-CAPS** (21 markers); **compose by intent** (don't load PR/commit/dependency sections for info-only questions).
- **Keep once each:** identity/self-reference, untrusted-comment safety, source-channel reply, autonomy.

### 5.5 Context management (already installed — just enable)
- Pass the sandbox backend so `FilesystemMiddleware` **tool-result eviction** offloads large `ToolMessage`s to `/large_tool_results/{id}` (re-readable preview) — attacks the 129k-token first calls.
- Built-in summarization offloads history to `/conversation_history/`. Tune thresholds; don't hand-roll.

### 5.6 Run budget + graceful degradation
- Lower `ModelCallLimitMiddleware` cap **5000 → ~250** + a wall-clock budget.
- On hitting either, an after-model hook **commits WIP + pushes + posts a "partial progress / stopping" reply** — never silent. Generalize today's `notify_step_limit_reached` to cover budget + timeout.

### 5.7 Idempotency (required by checkpoint replay)
- **Branch:** deterministic `open-swe/<thread-slug>`, derived in code.
- **PR:** create-or-update (`open_pull_request` already returns existing).
- **Replies:** dedupe by `(thread_id, content-hash)` persisted in thread state; the reply tool checks-then-posts.
- **Commit/push:** commit only if dirty; push is naturally idempotent.

---

## 6. Work in this PR (all of it ships together)

- **Dispatch core** — `dispatch.py` (`interrupt` + `durability="sync"` + `webhook`); delete is_thread_active/store-queue/`check_message_queue`/`_THREAD_RUN_LOCKS`; split webhooks out of `webapp.py`; route all triggers through it.
- **Completion + reconcile** — `completion.py` failure/timeout replies; `reconcile.py` sweep in `scheduler`; `disable_loopback:false`.
- **Remove PR-babysitting** — delete `ci_autofix.py`, retire `ci_monitor` graph, drop its webhook wiring.
- **Safety net** — shared timeout HTTP client; lower run budget; graceful-degradation reply.
- **Async tools** — rewrite the 29 `asyncio.run` tools; `requests`→`httpx`.
- **Agent assembly** — `agent_core.py`: unify model resolution, delete redundant middleware, wire `BackendFactory`, collapse sandbox recreate paths.
- **Prompt** — harness-profile replacement; repo setup → `repo_prep`; `memory=` for AGENTS.md; de-dupe; drop caps.
- **Context** — enable eviction (pass backend) + tune summarization.

---

## 7. Validation

- **Canary metric = the LangSmith finish-rate query**: finish rate (a `slack_thread_reply` present) and pending-orphaned %.
- Targets: finish rate **~60% → >95%**; pending-orphaned **36% → <3%**; work-losing `CancelledError` runs → ~0 (recycles resume from checkpoint).
- After merge, roll out behind a fraction/canary; watch finish rate, pending count, and `CancelledError` rate for 48 h before full traffic.

---

## 8. Out of scope (do not do in this PR)

- **Deepagents 0.6.12 bump** — not needed; every feature used here is in the installed 0.6.8 (reviewer/analyzer already use `skills=`). The bump's only real cost is langchain-1.x peer-deps (`langchain>=1.3.11,<2`, `langchain-core>=1.4.8,<2`, `langgraph 1.2.x`, `pydantic 2.13`), risky across the `langchain-*` sandbox integrations — unrelated to this rebuild. Leave it.
- **ci_autofix / PR-babysitting reintroduction** — removed here; revisit later on the new core if wanted.
- **Dashboard rewrite** (10.5k LOC) — keep as-is.
- **Reviewer / analyzer internals** — keep (they only inherit the shared dispatch/sandbox/model changes).

---

## Appendix — key refs
- Regression: `bb836b58` (#1251). Unmerged fix: `handle-hanging-threads-better` (`0a3d9234`, `7fa6c21b`).
- Dispatch today: `webapp.py:1283` (Slack) + other `runs.create` sites; `thread_ops.py:48` busy-check, `:18` in-process lock.
- Prompt: `prompt.py:530` (~7.5k tok). Sandbox: `server.py:408`. Pre-clone pattern: `repo_prep.py`.
- Platform: `durability=exit|async(default)|sync`; sweeper 2 min; shutdown grace 180s/max 3600; retries 3; hard 1h client-connection limit; completion `webhook` param; `runs.list(status=...)`/`cancel_many`.
- deepagents installed 0.6.8 (full-featured); latest 0.6.12; default middleware incl. Skills/Memory/eviction.
- Numbers: ~60% finish, 36% pending, 25.2M max tokens; `agent/` 33.8k LOC, `webapp.py` 3601; 6 graphs; cap 5000/recursion 9,999.
