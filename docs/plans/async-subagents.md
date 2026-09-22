# Async subagents as subthreads

## Decision

A subagent is a real `agent`-graph thread ("subthread") that shares the parent's
sandbox and starts from a fork of the parent's conversation. The parent drives it
with codex-style collaboration tools (`spawn_agent`, `wait_agent`, `send_message`,
`list_agents`, `close_agent`). There is no separate follow-up tool: every
message from parent to child interrupts the child and is handled at once.
Everything from child to parent (results, `send_message`) lands in the parent's
inbox, the same mailbox codex uses, and never interrupts. The UI renders subthreads
with the existing thread view, read-only, nested under the parent.

This replaces the in-process deepagents `task` tool in the main agent only. The
reviewer and chat graphs keep their synchronous subagents.

## Why a separate thread

- Fork mode falls out for free: same graph, same configurable (repo, workspace,
  login), same sandbox, so the child renders the identical system prompt and the
  copied history is a prompt-cache prefix hit.
- The transcript, thread view, cancel, cost, and trace plumbing already exist per
  thread. No new rendering path.
- Async is native: `dispatch_agent_run` already creates durable background runs
  with a completion webhook.

## Backend

### Tools (`agent/tools/subagents.py`, prompts under `agent/resources/prompts/tools/`)

| Tool | Args | Behaviour |
|---|---|---|
| `spawn_agent` | `task_name`, `message`, `model?: fast \| balanced \| performance`, `isolation?: shared \| worktree` (default `shared`) | Create child thread, dispatch forked run, return `{agent_id, task_name, model, worktree?}` |
| `merge_agent` | `target` | Worktree children only: rebase the child branch onto the parent's HEAD in the child's worktree, then `git merge --ff-only` in the main worktree. On conflict, abort and return the conflicting files |
| `wait_agent` | `timeout_s?` (clamp 30s to 10m, default 60s) | Block until any child reaches a terminal status or the parent's queue gets a subagent item; returns a status summary only |
| `send_message` | `target`, `message` | Parent → child: new run on the child with `multitask_strategy="interrupt"`; the in-flight step is cancelled, checkpointed history is kept, the message is handled immediately. Child → parent (`target = "parent"`): queued to the parent's inbox and delivered before its next model call, waking it if idle; never interrupts |
| `list_agents` | | One line per child: `task_name  status  model  age  last: <current tool or last text, ≤60 chars>` |
| `close_agent` | `target` | Cancel the child's run, mark closed, free a slot |

Observing a child in detail reuses the existing `get_thread` tool: a child id is a
thread id. `get_thread` gains `max_tokens: int = 2000` (chars ≈ 4 × tokens) that
budgets the transcript section. The transcript fills newest-first and includes
tool calls (name, args preview, output preview), not only human/AI text, so the
tail of the child's work is what fits.

`model` omitted inherits the parent's resolved model and effort. A tier resolves
through `settings.agent_routing_models` at spawn time and lands in the child's
configurable as `agent_model_id` / `agent_effort` with `model_selection: "explicit"`,
which `build_agent` already honours as a per-thread override.

Limits: max 4 live children per parent (codex "concurrency slots"), depth 1
(children never get the spawn tools). Tools are excluded in plan mode and for
automatic incident turns, same as `task` today.

### Spawn (`agent/subagents/spawn.py`)

1. `client.threads.create(metadata=...)` copying from the parent: `sandbox_id`,
   the sandbox proxy config key, repo fields, `workspace`, `owner_login`,
   `visibility`, participants, `source`; plus `parent_thread_id`,
   `subagent_task_name`, `title = task_name`, `unlisted: true`.
2. Build fork input like deepagents `_fork_messages`: parent messages with the
   offloading summary applied, trailing AI tool-call message dropped, then one
   `HumanMessage` with the fork preamble and the task. Reuse the existing
   `_FORK_TASK_PREAMBLE` text as a prompt file.
3. `dispatch_agent_run(child_id, input=..., configurable=child_cfg, source=parent source)`.
   Child configurable = parent configurable with `parent_thread_id`,
   `subagent_task_name`, model override, and a fresh invocation id.
4. Register the child in the LangGraph store: namespace
   `("subagents", parent_thread_id)`, key `child_id`, value
   `{task_name, model_id, effort, run_id, status, closed}`.

### Worktree isolation (opt-in, mildly discouraged)

Default is `shared`: children work in the parent's checkout and the parent tells
them which parts of the tree are theirs. The system prompt presents `worktree` as
an advanced option for tasks that must not see each other's uncommitted changes,
and notes the cost: worktrees share the object store but not untracked artifacts
(`node_modules`, venvs), so a worktree child may need to reinstall.

When `isolation="worktree"`:

- Spawn runs `git worktree add /workspace/.worktrees/<task_name> -b subagent/<task_name>`
  off the parent's current HEAD. The child's `work_dir` and rendered system prompt
  point at the worktree: commit to your branch, never switch branches, the parent
  merges.
- Spawn honours `.worktreeinclude` at the repo root, with Claude Code's rules:
  `.gitignore` syntax including `!` negations; only files that match a pattern
  **and** are gitignored are copied, never tracked files; copies, not symlinks;
  a `**/` pattern reaches into a wholly ignored directory only when that
  directory matches the pattern or the first name after `**/` is in its path.
  Implementation runs in the sandbox as shell: candidates from
  `git ls-files --others --ignored --exclude-standard -z`, matches from
  `git ls-files --others --ignored --exclude-from=.worktreeinclude -z`, copy the
  intersection with `tar` into the worktree. Skipped when the file is absent.
  The tool description tells the parent the file exists so it can ask for one
  when a worktree child needs `.env` or similar.
- The completion notification carries the branch, commits ahead of base, and
  whether the worktree is dirty.
- The parent merges with `merge_agent`. On conflict it either sends the child
  `send_message(child, "rebase onto <sha>, conflicts in X")` and calls
  `merge_agent` again, or resolves in the child's worktree itself via `execute`.
- `close_agent` removes the worktree and deletes the branch only if merged;
  otherwise it leaves both and says so.

### Child run shape (`agent/server.py`)

`RunConfig` gains `parent_thread_id: str | None` and `subagent_task_name`. When set:

- Tools: drop the parent-only set (`_is_subagent_excluded_tool` list) and
  `spawn_agent` / `wait_agent` / `list_agents` / `close_agent` / `merge_agent`. `send_message`
  stays and targets the parent. Everything else, including `background_execute`,
  stays.
- `PrepareAgentRunMiddleware`: skip title generation (title is the task name);
  keep sandbox reconnect, prompt render, usage record. `work_dir` comes from the
  child's `subagent_worktree` configurable when set, else the shared checkout.
- `ensure_sandbox_for_thread`: a subagent thread never replaces a gone sandbox;
  raise instead so it cannot bind a fresh empty box.
- The `_deepagents_forked_context` early return in `BasePrepareRunMiddleware`
  stays for the reviewer; the cross-thread fork does not set that key.

### Mailbox (`agent/completion.py`)

`handle_run_completion` for a thread with `parent_thread_id`:

1. Ignore status `interrupted`: that is a `send_message` replacing the run, not
   a finish. Update the store entry for `success`, `error`, `timeout` (status,
   final message text capped, error).
2. Queue a system envelope on the parent: sender `system:subagent`, body from a
   prompt file (`runs/subagent-finished.md`) with task name, status, and the
   child's final assistant message.
3. If the parent has no active run, dispatch a wake-up run on the parent
   (`multitask_strategy="enqueue"`), reusing the wake-up input shape from
   `schedule_thread_wakeup`. If the parent is running, `check_message_queue`
   injects it before the next model call.

A child's `send_message("parent", ...)` takes the same path as a completion
notification: queue a `system:subagent` envelope on the parent, wake the parent
if it has no active run. Nothing a child does ever interrupts the parent.
Interrupt semantics are parent → child only.

`wait_agent` polls the store entries every 2s until a status changes or timeout.
It returns "agent X finished" without the content; the content arrives via the
queue on the next model call. One delivery path, no dedupe.

### Cascade

- Cancel parent: cancel running children.
- Delete parent: delete children.
- Child cost: follow-up, roll up into the parent's session cost.

### Removal from the main agent

- `create_deep_agent(subagents=[])`; if deepagents still injects the
  general-purpose `task`, exclude `task` via `exclude_tools`.
- Delete `_general_purpose_subagent`, `_SubagentToolGuard`, `_subagent_middleware`,
  `_subagent_model_middleware`, `general-purpose-subagent-suffix.md`,
  `subagent-unavailable.md` (if the reviewer does not use it), and the subagent
  branch of `task_retry.py`.
- `shared-base.md` line about "delegating to a subagent" moves to a new
  `system/subagents.md` adapted from codex `collab/experimental_prompt.md`:
  when to spawn, shared filesystem warning, close when done, prefer long waits.

### API

- `GET /dashboard/api/threads/{id}/subagents` in `agent/threads/` returning thread
  summaries (reuse `_thread_summary`) for `metadata.parent_thread_id == id`.
- `_thread_summary` adds `parentThreadId` and `subagentTaskName`.
- Listing already hides `unlisted` threads.

## UI

- `AgentThread` gains `parentThreadId?`, `subagentTaskName?`.
- `useSubagentThreads(parentId)` query on the new endpoint, refetching every 3s
  while any child is `running`.
- Sidebar: children render indented under the parent row (same `SidebarThreadRow`,
  compact variant, no context menu), collapsible with the parent.
- Thread page: when `thread.parentThreadId` is set, render `AgentThreadView` with
  no composer, no model picker, and a "Subagent of <parent title>" link in the
  header. Existing `TranscriptSource` serves the transcript.
- Parent transcript: `spawn_agent` tool chunks render as a `SubthreadCard`
  (task name, model tier, live status from the query, link to the child)
  replacing `SubagentGroup` for that tool. `SubagentCard` stays for `task`
  (reviewer/chat).

## Phases (each its own PR off main, merged before the next)

1. Backend core: tools, spawn, child run shape, mailbox, cascade, prompts,
   removal of `task` from the main agent. ~2 days.
2. UI: types, query, sidebar nesting, read-only thread page, subthread card. ~1.5 days.
3. Cleanup: drop the now-unused subagent model settings (workspace settings,
   profile override, thread settings `subagent_model_id`, `/options` fields,
   `WorkspaceSettingsSections`, `cloud-agents.tsx`). ~0.5 day.

## Open items to verify while building

- `ConversationOffloadingMiddleware` state: how the offloaded view is stored so
  the fork can apply it (deepagents uses `SUMMARIZATION_EVENT_KEY`).
- `TranscriptMiddleware` on the child marks copied human messages as already
  seen, so the child transcript starts at the task prompt. Confirm.
- deepagents auto-adds a general-purpose subagent when `subagents` is empty
  (`agent/chat.py:83` comment). Confirm `task` is gone from the tool list.
- Desktop/local runs: sharing is trivial (same checkout path) but two agents
  editing the user's working tree concurrently is risky. Gate spawn tools off for
  `is_desktop_run` in phase 1.
