# Task lifecycle

## Decision

A **task** is a new first-class entity above threads and pull requests. It owns
every thread about it (root thread, subthreads, coordinators, fix and follow-up
threads, and each PR's reviewer and review scout threads) and every pull request it produces or adopts, across any
number of repositories.

A task has one or more owners (usually one), who are accountable, and zero or more assignees, who must act
next. There are no assignees while the agent is handling things. People are
assigned when the task needs them: reviewers (never the authors) while it waits
for human review, someone to resolve it when it is `blocked`, and the owners when
it is ready to merge with auto-merge off. `blocked` is its own state, separate
from waiting for review.

The task has one state. Its pull requests only contribute conditions (CI failing,
review needed, conflict) that the task's state is derived from.

An always-on, model-free **shepherd** carries the task from its first PR to merge.
On each relevant webhook it rereads the affected PR's conditions from GitHub,
recomputes the task's state, keeps one timeline per task, wakes the task's driver
thread only when there is work the agent can do, and merges (or announces
readiness) once every PR in the task is mergeable.

The shepherd replaces `/baby-sit` and the dead auto-fix plumbing entirely. Any
human-authored PR can be taken over and enter the same cycle.

Deployment, post-deploy verification, and rollback are a later phase. The state
model leaves room for them (see [Later](#later)).

## Why

Today each step has its own mechanism, its own state, and its own gaps:

| Step | Today | Gap |
|---|---|---|
| CI | `agent/baby_sit.py`, opt-in per PR via `/baby-sit` | Nothing watches CI unless someone asks. The agent opens a PR and stops |
| Automated review → fix | `reviewer` graph publishes findings | Findings never reach the coding thread: `agent/github/routes.py` drops events from `open-swe[bot]` because it is not a registered user. A human must relay them |
| Human review | Untagged comments and reviews wake the agent on PRs it opened | Only for PRs the agent opened; nothing for adopted PRs |
| Merge readiness | `agent/expedited_review/readiness.py` | Only expedited PRs get a readiness verdict or a merge |
| Auto-fix | `agent/dashboard/autofix_state.py`, `("autofix", thread_id)` store events, `runs/autofix-event.md` | The consumer in `check_message_queue.py` still runs, but nothing produces events and `is_pr_autofix_disabled` has no callers |
| Status | Thread metadata, store namespaces, crons, Slack card state | No single answer to "where is this work?" for the UI, Slack, or the agent |

A PR is also the wrong unit. One task routinely spans repos (an SDK change plus
its consumer), and with subthreads and coordinators ([async subagents plan](https://github.com/langchain-ai/open-swe/pull/3074))
one task spans threads too. Merge and readiness have to be decided for the whole
task.

## Model (`agent/tasks/models.py`)

```
task
  id                uuid7
  title             text
  goal              text      what done looks like, from create_task
  driver_thread_id  text      thread the shepherd wakes
  auto_merge        bool null null = use the owners' preferences
  state             text      derived, cached (see Task state)
  created_at, closed_at
  close_reason      completed | abandoned | null
  last_progress_at  timestamptz drives staleness
  stale_at          timestamptz null

task_thread
  task_id           uuid
  thread_id         text      at most one open task per thread
  primary key (task_id, thread_id)
  role              root | sub | coordinator | fix | followup | reviewer | review_scout
  pull_request_id   uuid null the PR a reviewer or review_scout thread belongs to

task_pull_request
  pull_request_id   uuid PK   -> pull_request; a PR belongs to at most one task
  task_id           uuid
  source            opened | takeover
  added_by_user_id  uuid null
  merge_after       uuid[]    pull_request_ids that must merge first
  conditions        jsonb     typed list, see PR conditions; not a state
  evaluated_sha     text
  evaluated_at      timestamptz

task_owner                     accountable people; at least one, usually one
  task_id           uuid
  user_id           uuid      -> user
  added_by          uuid null
  added_at          timestamptz
  primary key (task_id, user_id)

task_assignee                  who must act now; no rows = the agent has it
  task_id           uuid
  user_id           uuid      -> user
  reason            review | blocked | merge | manual
  pull_request_id   uuid null the PR it concerns
  assigned_by       uuid null null = the shepherd
  assigned_at       timestamptz
  unique (task_id, user_id, reason, pull_request_id)

task_event                     append-only timeline
  id, task_id, pull_request_id null, kind, from_state, to_state,
  head_sha, detail jsonb, created_at

task_wakeup                    dedupe + retry budget
  pull_request_id, head_sha, reason   unique
  task_id, created_at
```

### Membership

- The agent creates a task explicitly with `create_task(title, goal, owners)` as soon as
  it decides the work will change code, before any PR exists. The calling thread
  becomes the root and the driver. A takeover also creates a task when the PR is
  not being added to an existing one.
- A thread has at most one open task. Once it closes (completed or abandoned),
  the same thread can create another, so a long-lived Slack or concierge thread
  can carry several tasks in sequence.
- The thread's open task is "in scope" for its runs, and for its subthreads.
- A subthread (`parent_thread_id` set) joins its parent's task as `sub`.
- A thread started from the dashboard PR actions (`agent/threads/pr_fixes.py`)
  joins the PR's task as `fix` when one exists.
- Each PR's reviewer thread (`reviewer_thread_id`) and review scout thread
  (`review_scout_thread_id`) join the task as `reviewer` and `review_scout` when
  the PR joins, or when they are created later for a PR already in a task. They
  leave with the PR on release. Reviewer and scout threads for PRs outside any
  task are unchanged.
- The shepherd never wakes a `reviewer` or `review_scout` thread; the existing
  review triggers keep driving them. Owning them puts every thread about the task
  (building, reviewing, and explaining it) in one place: the task panel, the
  timeline, cost, and `get_task`.
- `pull_request_thread` links are unchanged. `task_pull_request` records
  ownership; `pull_request_thread` still records which threads touched a PR.
- `create_task` requires `owners`: one or more of the thread's verified
  participants (`participant_logins` and `participant_emails` in
  `agent/utils/thread_participants.py`), each resolving to a registered user.
  The tool rejects anyone outside that roster, so the agent can never make an
  outsider accountable. Usually that is just the person who asked; the agent
  adds others only when they asked to share the work.
- A takeover makes the person who took the PR over the owner of the new task.
- Owners added later by the agent (`set_task_options`) must also be thread
  participants. People can add any registered user as an owner from the task
  panel or with `@open-swe owner add|remove @login` on a PR. The last owner
  cannot be removed.
- `PullRequest.agent_thread_id` callers move to the task's driver thread, so
  adopted PRs behave the same as agent-opened ones.

## Task state (`agent/tasks/state.py`)

The task has the state. Pull requests have no state of their own in this model;
each contributes **conditions**, facts read from GitHub, and the task's state is
derived from the conditions of all its PRs plus task-level facts (a block, an
active merge, whether it is closed).

### PR conditions

A pure function turns a PR snapshot into its list of conditions. The snapshot
generalizes `PullRequestSnapshot` from `expedited_review/readiness.py` (GitHub
open/merged/closed, draft, mergeability, check runs, commit statuses, required
checks, reviews, unresolved review threads) and adds the latest Open SWE review
for the head SHA from `pull_request_review`. Every condition that applies is
listed, not just the first.

| Condition | Who acts | Source |
|---|---|---|
| `conflict` | agent (shepherd wake-up) | mergeability |
| `ci_failed` (check names, URLs) | agent (shepherd wake-up) | failing required checks, or any failing check the agent can reproduce |
| `bot_findings` (finding ids) | agent (shepherd wake-up) | unresolved Open SWE findings on the head SHA |
| `changes_requested` (reviewers) | agent (existing comment wake-ups) | latest human review state |
| `unresolved_threads` | agent (existing comment wake-ups) | review threads |
| `ci_pending` | nobody, waiting on automation | running or unreported required checks |
| `bot_review_pending` | nobody, waiting on automation | repo reviewed by Open SWE and no review for the head SHA |
| `review_needed` (reviewers) | assigned reviewers | an assigned reviewer has not reviewed the head, or branch protection requires approvals not yet given |

Drafts drop the review conditions. A PR with no conditions is mergeable. Merged
and closed PRs contribute no conditions.

### States

The task's state is the first rule that matches:

| State | When | Assignees |
|---|---|---|
| `completed` / `abandoned` | the task is closed | none |
| `stale` | no progress for 2 working days (see [Stale](#stale)) | none |
| `blocked` | the task has an open block | whoever must resolve it |
| `merging` | an auto-merge sequence is running | none |
| `in_progress` | the task has no PRs yet, or any PR has an agent-actionable condition | none, the agent has it |
| `in_review` | any PR has `review_needed` | the reviewers |
| `waiting_on_checks` | any PR has `ci_pending` or `bot_review_pending` | none |
| `ready_to_merge` | every open PR is mergeable | the owners, when auto-merge is off |
| `merged` | every PR is merged or closed, and at least one merged | none |

The task panel shows the state once, with each PR's conditions underneath, for
example "In review: sdk#41 waiting on @alex; app#88 CI running".

A task closes as `completed` when it reaches `merged` and the agent does not
open another PR within its run, or explicitly through `close_task(reason)`
(`completed` or `abandoned`). Abandoning a task with open PRs asks the agent to
close or release them first.

## Shepherd (`agent/tasks/shepherd.py`)

### Triggers

`reevaluate(pull_request_id)` recomputes that PR's conditions under the existing
per-PR lock, then recomputes its task's state. It is called from:

- `pull_request` webhooks (every action), `push` to a task PR's head ref,
  `GITHUB_CI_EVENTS`, `pull_request_review`, `pull_request_review_comment`
- `publish_review` right after the reviewer posts, so bot findings are seen
  without depending on a bot-authored webhook
- takeover and release
- one global sweep cron (every 10 minutes) over PRs of open, non-stale tasks
  whose `evaluated_at` is older than 10 minutes. This catches base-branch changes
  that alter mergeability and any lost webhook. Unchanged state costs no model
  tokens. The same sweep enforces review `due_at` and marks tasks stale

Each evaluation writes the PR's `conditions` and `evaluated_sha`, the task's
`state`, and a `task_event` when either changes.

### Wake-ups

When shepherd-actionable conditions (`conflict`, `ci_failed`, `bot_findings`)
appear on a PR's head SHA, the shepherd inserts `task_wakeup(pr, sha, reason)`
and, only if the insert succeeded, dispatches one run to the driver thread
through the normal queue. The prompt
(`agent/resources/prompts/runs/task-wakeup.md.jinja`) names the repo, PR, head
SHA, and every actionable condition at once, so a PR failing CI with open
findings gets one wake-up, not two.

Human comments and reviews keep flowing through the existing untagged-comment
path, which already carries the text. The shepherd only records the resulting
conditions.

Retry budget: after 5 wake-ups on one PR without any of its actionable conditions
clearing (a new head SHA alone is not progress), the task is blocked with reason
`retry_budget_exhausted` on that PR.

### Blocked

`blocked` means something went wrong or is missing, and the task cannot move
until a person resolves it. It is separate from `in_review`, which is
the expected hand-off to reviewers.

A block records `reason`, a one-paragraph `ask` addressed to the person, and the
PR it applies to (or none for a task-wide block). Entering `blocked` assigns the
task (see [Assignment](#assignment)). Reasons:

| Reason | Entered by |
|---|---|
| `agent_request` | The agent, via `request_human`, when it needs a decision, access, or information it cannot get: an ambiguous review comment, conflicting reviewer asks, a secret, a product call |
| `retry_budget_exhausted` | The shepherd, after 5 wake-ups without progress |
| `merge_failed` | The shepherd, when an auto-merge sequence stops partway |
| `permission_denied` | The shepherd, when a push, merge, or check read fails on GitHub permissions (a fork without `maintainer_can_modify`, a protected branch, a missing App permission) |
| `no_reviewer` | The shepherd, when a PR needs human review and no eligible reviewer can be found (see [Assignment](#assignment)) |

While blocked, the shepherd keeps evaluating and recording the timeline but sends
no wake-ups for the blocked PR (all PRs, for a task-wide block), and auto-merge
does not run.

A block clears when:

- an assignee or an owner replies in the task's thread, Slack thread, or on
  the PR. The reply wakes the driver thread with the block's `ask` and the answer
- the person clicks "Unblock" in the task panel, optionally with a note
- the cause goes away on its own (for example, a human pushes a fix that turns
  CI green, or a permission is granted), which the next evaluation detects. The
  shepherd never clears an `agent_request` block on its own

Clearing a block resets the PR's retry budget, removes the `blocked` assignments,
and records a `task_event`.

### Stale

A task that has not made progress for **2 working days** becomes `stale`, and the
shepherd forgets it. This is deliberately aggressive: a task nobody is moving
should stop generating noise.

Progress is any of: a new commit on any of the task's PRs, a PR condition
clearing, a review submitted, a PR merged, a block answered, a new PR added, or a
message from an owner or assignee on the task. Wake-ups, reassignments, and
nudges are not progress. So a task sitting in `in_review` with no review and no
commits for 2 working days goes stale, as does a `blocked` task nobody answered.

This is separate from the retry budget, where a new commit that clears nothing
does not count: an agent looping on CI keeps the task fresh but still runs out of
budget and gets blocked.

Working days are weekdays in the owners' time zones, from the stored working
hours. `task.last_progress_at` records the latest progress event, and the global
sweep marks tasks stale.

Once stale:

- no reevaluation from webhooks or the sweep, no wake-ups, no review requests,
  reassignments, nudges, or notifications
- all assignments are removed and the task's GitHub review requests are withdrawn
- the owners get one notification that it went stale, with a Resume link
- it still owns its threads and PRs, and still counts as the thread's task, so
  `create_task` in that thread fails until an owner resumes or abandons it

Only an owner can bring it back, explicitly: the Resume button in the task panel,
`@open-swe resume` on one of its PRs, or asking the agent in the thread, which
calls `resume_task` and is rejected unless the requester is an owner. Resuming
reevaluates every PR, resets retry budgets, restarts the 2-day clock, and picks
reviewers afresh. An owner can also abandon it instead.

### Assignment

A task has zero or more assignees: the people who must act next. With none, the
agent has it. Owners change only when someone edits them; assignees come and go. Each assignment has a
reason, so one person can be assigned twice for different things (to review one
PR and to unblock another).

The task's **authors** are every owner, every PR author, and whoever took a PR
over. Authors are never assigned to review.

Automatic assignment, applied on task state transitions:

| Task state | Assignees | Reason |
|---|---|---|
| `in_review` | the reviewers of each PR with `review_needed`, excluding authors | `review` |
| `blocked` | the people named in `request_human`; otherwise the owners, plus the PR author for a block on a taken-over PR | `blocked` |
| `ready_to_merge` with auto-merge off | the owners | `merge` |
| anything else | none from the shepherd | |

#### Picking reviewers (`agent/tasks/reviewers.py`)

The shepherd picks reviewers for a PR once it has no agent-actionable or
automation conditions left (CI green, Open SWE review done, nothing for the agent
to fix) and still lacks the approvals it needs. From then on the PR carries
`review_needed` with those reviewers. It does not use GitHub's team auto-assignment or reviewer
suggestions. A reviewer a person explicitly requested (on GitHub, in the panel,
or through `assign_task`) is always kept, and the picker only fills the remaining
slots.

A review is always assigned to an individual, never to a team. A team in
`CODEOWNERS` expands to its members, who are scored like anyone else. When GitHub
requests a team (for example through `CODEOWNERS` auto-requests), the shepherd
replaces the team request with a request to the one member it picks; that
member's approval still satisfies code owner review. By default a PR gets one
reviewer, and more only when the rules below require them.

Candidates are people with write access to the repo who are not authors, drawn
from `CODEOWNERS` entries for the changed paths and from recent committers and
reviewers of the changed files.

Each candidate is scored on:

| Signal | Source |
|---|---|
| Code ownership | `CODEOWNERS` match on changed paths, weighted by lines changed under each path |
| Change history | commits to the changed files in the last 180 days, weighted by recency and lines touched, from the GitHub commits API per path (top 20 files by lines changed) |
| Review history | reviews on earlier PRs touching the same files |
| Load | open review requests across the org plus active `review` assignments in Open SWE; each one lowers the score |

The pick is the smallest set that:

- includes a code owner for every owned path when branch protection requires
  code owner review, preferring one person who covers several paths
- meets the repo's required approval count (1 when the repo requires none)
- takes the highest-scoring remaining candidates to fill any remaining slots

Every pick carries a one-line rationale built from its signals, shown in the task
panel and in the assignment notification, for example "code owner of
`agent/tasks/`, 14 commits to these files in 90 days, 2 open reviews". Scores are
cached per repo and path for an hour. When no candidate qualifies, the task is
blocked with `no_reviewer`, assigned to the owners, whose answer (or a manual
assignment) picks the reviewers.

#### Acknowledgement and reassignment

Each review slot has exactly one assignee at a time. The assignee must
acknowledge within **2 working hours** or the slot moves to the next-best
candidate.

- Acknowledging means clicking "Start review" in the Slack DM or task panel, or
  any review activity on the PR: a review comment, a submitted review, or opening
  the PR's Open SWE review page.
- "Pass" in the Slack DM or task panel reassigns immediately.
- On timeout or pass, the shepherd picks the next candidate from the same pool
  (the same code-owner team, for a code-owner slot), moves the GitHub review
  request, notifies both people, and records a `task_event`. Someone who timed out
  or passed is not picked again for that PR.
- The 2-hour clock counts only the assignee's working hours (see
  [Working hours](#working-hours)), so a review assigned overnight does not
  rotate through the team before anyone is awake.
- Once acknowledged, the slot is no longer reassigned automatically. If no review
  arrives within 1 working day, the shepherd nudges the reviewer once and
  notifies the owners.
- When the pool runs out, the task is blocked with `no_reviewer`.

`task_assignee` gains `acknowledged_at`, `due_at`, and `passed_user_ids` for this.
#### Working hours

Each user row stores `time_zone` (IANA name), `time_zone_synced_at`, and
`working_hours` (default weekdays 09:00–18:00, editable in settings). Keeping
them in Postgres makes "who can review right now?" a single query:
`now() AT TIME ZONE time_zone` compared against `working_hours`, joined with
review load.

- `time_zone` updates whenever Slack hands us the user's profile anyway (the
  Slack webhook already reads `tz` on every mention), and on assignment when
  `time_zone_synced_at` is older than 7 days. No separate polling.
- A user without a linked Slack account uses the workspace time zone.
- The picker prefers candidates inside working hours right now, so a review goes
  to someone who can pick it up. When nobody in the pool is working, it picks
  the best candidate anyway and the clock starts at their next working hour.
- `due_at` is computed once at assignment from the stored values; the sweep only
  compares `due_at`.

Assignment and GitHub review requests stay in sync in both directions. Assigning a
reviewer in Open SWE requests their review on the PR, and a review request a
person makes on GitHub adds the assignment. A reviewer's `review` assignment ends when they
submit a review on the current head: an approval removes it, and a
changes-requested review removes it and returns the PR to the agent. When the
agent pushes a fix, the shepherd re-requests review from those reviewers and they
are assigned again. A PR with several reviewers keeps all of them assigned until
each has reviewed.

Manual assignment: anyone who can see the task can add or remove assignees from
the task panel, from chat or Slack ("have @alex and @sam review this") through
`assign_task`, or with `@open-swe assign @login` on a PR. Manual assignments
(`reason = manual`) survive task state transitions until the person acts (reviews,
replies, unblocks, merges) or is removed. Assigning a person while the agent is
working does not stop the agent.

Every change records a `task_event`. Each new assignee is notified once, through a
Slack DM and a banner in the task panel carrying the reason: the block's `ask`,
"review <PR>", or "ready to merge". A blocked task also gets a comment on the
affected PR.

### Merge

When the task state becomes `ready_to_merge`:

- `auto_merge = task.auto_merge ?? all(o.preferences.auto_merge_shepherded_prs for o in owners)`.
  With several owners and no override, every owner must have opted in.
- Off: assign the task to the owners and post "ready to merge" once to the task
  thread, Slack thread, and each PR.
- On: the task moves to `merging` and merges PRs in `merge_after` order (topological; independent PRs in any
  order) using each repo's merge method, via the merge code generalized out of
  `expedited_review/merge.py`. Re-evaluate each PR immediately before merging it.
  If a merge fails or a PR gains a condition mid-sequence, stop and block the
  task with `merge_failed`, listing which PRs merged.

## Entry points

### Agent-opened PRs

`open_pull_request` adds the PR to the task in scope and fails without one,
returning an error that tells the agent to call `create_task` first. No opt-in
beyond that.

### Tools gated on a task

Code-changing tools require a task in scope; everything else stays available, so
questions, investigations, and reviews never need a task.

| Gated | Why |
|---|---|
| `write_file`, `edit_file` | Direct file changes |
| `open_pull_request`, `link_pull_request` | PRs always belong to a task |
| `git push` through the sandbox GitHub proxy | Catches changes made through `execute` |

`execute` itself stays ungated because investigation needs it (running tests,
reading logs, `git log`); the push check in the proxy is what stops untracked
work from leaving the sandbox. A gated call without a task returns an error
naming `create_task`. The gate is one middleware in `agent/middleware/` plus the
proxy check. Local runs get the middleware but not the push check, since they push
from the user's machine without the proxy. The system prompt tells the agent to
create a task before changing code.

Nothing creates a task implicitly on the agent's behalf: not thread creation,
not the first edit, not `open_pull_request`. The only paths are the agent's
`create_task` call and a human takeover.

### Takeover

A registered Open SWE user with write access to the repo can hand any open PR to
the agent:

- Dashboard: a "Shepherd this PR" action (`ShepherdIntent` next to `FixIntent`
  in `agent/threads/pr_fixes.py`), into a new task or an existing one
- GitHub: `@open-swe shepherd` on the PR
- Agent: `link_pull_request(..., shepherd=True)`, so "take over #123" or "add
  #123 to this task" works from Slack and chat

On takeover the shepherd posts a PR comment naming who handed it over and how to
release it, then evaluates immediately. The driver thread's first wake-up
includes the full current state (CI, findings, unresolved threads) so it can
catch up in one run.

Fork PRs are accepted only when `maintainer_can_modify` is true; otherwise the
takeover is refused with that reason.

### Release

`@open-swe release`, a dashboard button, or `release_pull_request`. The PR author
and any task owner can always release. Release removes the `task_pull_request`
row, records a `task_event`, and posts a PR comment.

## Settings

- `UserPreferences.auto_merge_shepherded_prs: bool = False`
  (`agent/users/preferences.py`), in the dashboard settings.
- Per-task override `task.auto_merge`, set from the task panel toggle, from chat
  ("merge when ready", "don't auto-merge") through `set_task_options`, or with
  `@open-swe automerge on|off` on any of the task's PRs.
- Taking a PR over into a new task makes the person who took it over the owner;
  adding it to an existing task leaves that task's owners unchanged.

## Agent tools (`agent/tools/tasks.py`)

| Tool | Behaviour |
|---|---|
| `create_task` | `title`, `goal`, `owners: [login \| email]` (thread participants only, at least one); creates the thread's open task. Fails if one is already open |
| `close_task` | `reason: completed \| abandoned` |
| `get_task` | Task state, PRs with their conditions, recent timeline |
| `resume_task` | Moves a stale task back into the cycle; only when the requesting participant is an owner |
| `link_pull_request` | Gains `shepherd: bool`; adds the PR to the thread's task |
| `release_pull_request` | Remove a PR from the task |
| `set_task_options` | `auto_merge: bool \| null`, `merge_after: {pr: [prs]}`, `owners: {add: [login \| email], remove: [login \| email]}` (added owners must be thread participants) |
| `request_human` | `ask`, `pull_request?`, `assignees?: [login]`; blocks the task (or one PR) with `agent_request`, assigns it, and ends the run |
| `assign_task` | `add: [login]`, `remove: [login]`, `reason: review \| manual`, `pull_request?`; `review` also requests review on GitHub |

Tool descriptions live under `agent/resources/prompts/tools/`.

## Deleted

- `agent/baby_sit.py`, `agent/tools/manage_baby_sit.py`,
  `agent/bundled_skills/baby-sit/`, prompts `tools/manage_baby_sit.md` and
  `runs/baby-sit-failure.md.jinja`, the `baby_sit_watches` store namespace and
  `baby_sit_watch` crons, and their references in `scheduler.py`, `dispatch.py`,
  `thread_ids.py`, `source_context.py`, `server.py`, `github/webhook.py`, and
  `ui/src/features/agents/lib/queries.ts`
- Auto-fix leftovers: `agent/dashboard/autofix_state.py`, the autofix consumer in
  `middleware/check_message_queue.py`, `runs/autofix-event.md`, the
  `("autofix",)` entry in `slack/stop.py`, and the `autofix_*` workspace settings
- `expedited_review/readiness.py` moves to `agent/tasks/readiness.py`; expedited
  review keeps its voting and card and reads readiness from the shepherd

Migration: backfill a task for every open PR with an `open_pull_request` link,
with its linked agent thread as root and driver, then drop the baby-sit crons.
Active watches need no conversion because the backfilled task covers the same PR.
A thread that is mid-change without a PR at deploy time hits the gate on its next
edit and gets the `create_task` error, which is the intended path.

## Later

Task states extend past `merged` without changing the model: `deploying`, `deployed`,
`verified`, driven by GitHub `deployment_status` or a configured deploy workflow
per repo. A rollback is a revert PR added to the same task, which then runs the
same cycle.

## Rollout

1. Backend core: schema and migration, `create_task` / `close_task` / `get_task`,
   the tool gate, PR conditions and task state, shepherd triggers, wake-ups and
   budget, staleness, backfill, deletions. About 3 days.
2. Merge: shared readiness, auto-merge preference and override, merge ordering,
   `set_task_options`. About 1 day.
3. Takeover and release across dashboard, GitHub, and tools. About 1 day.

## Open questions

- On a taken-over PR, should comments from a PR author who is not a registered
  Open SWE user wake the driver thread? Today they are dropped.
- Is 5 the right retry budget, and should it differ for CI versus findings?
- Should a reviewer who is not a registered Open SWE user be assignable? GitHub
  review requests work for them, but they get no Slack DM or dashboard view.
- Should reviewer scoring also skip people who are away (Slack status, calendar),
  and learn from how quickly each person has reviewed before?
