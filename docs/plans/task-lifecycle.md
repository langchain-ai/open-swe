# Task lifecycle

## Decision

A **task** is a new first-class entity above threads and pull requests. It owns
every thread working on it (root thread, subthreads, coordinators, fix and
follow-up threads) and every pull request it produces or adopts, across any
number of repositories.

A task has one or more owners (usually one), who are accountable, and zero or more assignees, who must act
next. There are no assignees while the agent is handling things. People are
assigned when the task needs them: reviewers (never the authors) while it waits
for human review, someone to resolve it when it is `blocked`, and the owners when
it is ready to merge with auto-merge off. `blocked` is its own state, separate
from waiting for review.

An always-on, model-free **shepherd** tracks each of the task's pull requests from
open to merge. It recomputes a stage for every PR from GitHub state on each
relevant webhook, keeps one timeline per task, wakes the task's driver thread only
when there is work the agent can do, and merges (or announces readiness) once
every PR in the task is ready.

The shepherd replaces `/baby-sit` and the dead auto-fix plumbing entirely. Any
human-authored PR can be taken over and enter the same cycle.

Deployment, post-deploy verification, and rollback are a later phase. The stage
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
  driver_thread_id  text      thread the shepherd wakes
  auto_merge        bool null null = use the owners' preferences
  stage             text      derived, cached (see Task stage)
  created_at, closed_at

task_thread
  thread_id         text PK   a thread belongs to at most one task
  task_id           uuid
  role              root | sub | coordinator | fix | followup

task_pull_request
  pull_request_id   uuid PK   -> pull_request; a PR belongs to at most one task
  task_id           uuid
  source            opened | takeover
  added_by_user_id  uuid null
  merge_after       uuid[]    pull_request_ids that must merge first
  stage             text
  blockers          jsonb     typed list, see Stages
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
  id, task_id, pull_request_id null, kind, from_stage, to_stage,
  head_sha, detail jsonb, created_at

task_wakeup                    dedupe + retry budget
  pull_request_id, head_sha, reason   unique
  task_id, created_at
```

### Membership

- A task is created lazily the first time a thread links a PR
  (`open_pull_request`, `link_pull_request`, or a takeover). That thread becomes
  the root and the driver.
- A subthread (`parent_thread_id` set) joins its parent's task as `sub`.
- A thread started from the dashboard PR actions (`agent/threads/pr_fixes.py`)
  joins the PR's task as `fix` when one exists.
- `pull_request_thread` links are unchanged. `task_pull_request` records
  ownership; `pull_request_thread` still records which threads touched a PR.
- The first owner is the person who started the root thread (or took the PR
  over). Any owner can add or remove owners from the task panel, from chat or
  Slack through `set_task_options`, or with `@open-swe owner add|remove @login`
  on a PR. The last owner cannot be removed.
- `PullRequest.agent_thread_id` callers move to the task's driver thread, so
  adopted PRs behave the same as agent-opened ones.

## Stages (`agent/tasks/stages.py`)

A pure function evaluates a PR snapshot into a stage plus a blocker list. The
snapshot generalizes `PullRequestSnapshot` from `expedited_review/readiness.py`
(state, draft, mergeability, check runs, commit statuses, required checks,
reviews, unresolved review threads) and adds the latest Open SWE review for the
head SHA from `pull_request_review`.

Blockers (all that apply, not just the first):

| Blocker | Actionable by agent | Source |
|---|---|---|
| `conflict` | yes | mergeability |
| `ci_failed` (check names, URLs) | yes | failing required checks, or any failing check the agent can reproduce |
| `bot_findings` (finding ids) | yes | unresolved Open SWE findings on the head SHA |
| `changes_requested` (reviewers) | via existing comment wake-ups | latest human review state |
| `unresolved_threads` | via existing comment wake-ups | review threads |
| `ci_pending` | no | running or unreported required checks |
| `bot_review_pending` | no | repo reviewed by Open SWE and no review for head SHA |
| `human_review_pending` (reviewers) | no | a requested or assigned reviewer has not reviewed the head, or branch protection requires approvals not yet given |

Stage is the highest-priority label: `merged`, `closed`, `blocked`, `conflict`,
`ci_failed`, `changes_requested`, `ci_pending`, `bot_review_pending`,
`human_review_pending`, `ready`. Drafts evaluate the same way except that review
blockers are dropped.

### Task stage

Derived from its open PRs: `ready` when every PR is `ready`, `merged` when every
PR is merged or closed with at least one merged, otherwise the worst PR stage.
The UI shows the breakdown ("2 of 3 ready").

## Shepherd (`agent/tasks/shepherd.py`)

### Triggers

`reevaluate(pull_request_id)` runs under the existing per-PR state lock and is
called from:

- `pull_request` webhooks (every action), `push` to a task PR's head ref,
  `GITHUB_CI_EVENTS`, `pull_request_review`, `pull_request_review_comment`
- `publish_review` right after the reviewer posts, so bot findings are seen
  without depending on a bot-authored webhook
- takeover and release
- one global sweep cron (every 10 minutes) over open task PRs whose
  `evaluated_at` is older than 10 minutes. This catches base-branch changes that
  alter mergeability and any lost webhook. Unchanged state costs no model tokens

Each evaluation writes `stage`, `blockers`, `evaluated_sha`, and a `task_event`
when the stage changes.

### Wake-ups

When actionable blockers (`conflict`, `ci_failed`, `bot_findings`) appear for a
head SHA, the shepherd inserts `task_wakeup(pr, sha, reason)` and, only if the
insert succeeded, dispatches one run to the driver thread through the normal
queue. The prompt (`agent/resources/prompts/runs/task-wakeup.md.jinja`) names the
repo, PR, head SHA, and every actionable blocker at once, so a PR failing CI with
open findings gets one wake-up, not two.

Human comments and reviews keep flowing through the existing untagged-comment
path, which already carries the text. The shepherd only records their effect on
the stage.

Retry budget: after 5 wake-ups on one PR without the stage improving (a new head
SHA alone is not progress), the PR becomes `blocked` with reason
`retry_budget_exhausted`.

### Blocked

`blocked` means something went wrong or is missing, and the task cannot move
until a person resolves it. It is separate from `human_review_pending`, which is
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

### Assignment

A task has zero or more assignees: the people who must act next. With none, the
agent has it. Owners change only when someone edits them; assignees come and go. Each assignment has a
reason, so one person can be assigned twice for different things (to review one
PR and to unblock another).

The task's **authors** are every owner, every PR author, and whoever took a PR
over. Authors are never assigned to review.

Automatic assignment, applied on stage transitions:

| Stage | Assignees | Reason |
|---|---|---|
| `human_review_pending` | the PR's reviewers, excluding authors | `review` |
| `blocked` | the people named in `request_human`; otherwise the owners, plus the PR author for a block on a taken-over PR | `blocked` |
| `ready` with auto-merge off | the owners | `merge` |
| anything else | none from the shepherd | |

#### Picking reviewers (`agent/tasks/reviewers.py`)

The shepherd picks reviewers itself when a PR first reaches
`human_review_pending`. It does not use GitHub's team auto-assignment or reviewer
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
- The 2-hour clock counts only the assignee's working hours (weekdays 09:00–18:00
  in their Slack time zone), so a review assigned overnight does not rotate
  through the team before anyone is awake.
- Once acknowledged, the slot is no longer reassigned automatically. If no review
  arrives within 1 working day, the shepherd nudges the reviewer once and
  notifies the owners.
- When the pool runs out, the task is blocked with `no_reviewer`.

`task_assignee` gains `acknowledged_at`, `due_at`, and `passed_user_ids` for this.
The assignee's time zone is read from Slack (`users.info`) once, when the
assignment is made, and used to compute `due_at`. The global sweep compares only
the stored `due_at`, so there is one Slack call per assignment and no cache. A
user without a linked Slack account falls back to the workspace time zone.

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
(`reason = manual`) survive stage transitions until the person acts (reviews,
replies, unblocks, merges) or is removed. Assigning a person while the agent is
working does not stop the agent.

Every change records a `task_event`. Each new assignee is notified once, through a
Slack DM and a banner in the task panel carrying the reason: the block's `ask`,
"review <PR>", or "ready to merge". A blocked task also gets a comment on the
affected PR.

### Merge

When the task stage becomes `ready`:

- `auto_merge = task.auto_merge ?? all(o.preferences.auto_merge_shepherded_prs for o in owners)`.
  With several owners and no override, every owner must have opted in.
- Off: assign the task to the owners and post "ready to merge" once to the task
  thread, Slack thread, and each PR.
- On: merge PRs in `merge_after` order (topological; independent PRs in any
  order) using each repo's merge method, via the merge code generalized out of
  `expedited_review/merge.py`. Re-evaluate each PR immediately before merging it.
  If a merge fails or a PR stops being ready mid-sequence, stop and block the
  task with `merge_failed`, listing which PRs merged.

## Entry points

### Agent-opened PRs

`open_pull_request` creates or joins the thread's task. No opt-in.

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
| `get_task` | Task, PRs with stage and blockers, recent timeline |
| `link_pull_request` | Gains `shepherd: bool`; adds the PR to the thread's task |
| `release_pull_request` | Remove a PR from the task |
| `set_task_options` | `auto_merge: bool \| null`, `merge_after: {pr: [prs]}`, `owners: {add: [login], remove: [login]}` |
| `request_human` | `ask`, `pull_request?`, `assignees?: [login]`; blocks the task (or one PR) with `agent_request`, assigns it, and ends the run |
| `assign_task` | `add: [login]`, `remove: [login]`, `reason: review \| manual`, `pull_request?`; `review` also requests review on GitHub |

Tool descriptions live under `agent/resources/prompts/tools/`.

## API and UI

- Router in `agent/tasks/routes.py` under `/dashboard/api/tasks`: get task,
  timeline, set options, assign, unblock, takeover, release, and "assigned to
  me" listing.
- Thread right panel gets a Task section: each PR with repo, stage chip, and
  blockers; the task stage; the timeline; the auto-merge toggle; assignees with
  their reasons and a multi-person picker. A blocked task shows the `ask` as a
  banner with an Unblock button.
- Sidebar thread rows show the task stage chip and assignee avatars (none when the
  agent has it). Tasks assigned to the viewer count toward their attention
  indicator, and the sidebar gets an "Assigned to me" filter.
- A standalone tasks list is out of scope for the first pass.

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

## Later

Stages extend past `merged` without changing the model: `deploying`, `deployed`,
`verified`, driven by GitHub `deployment_status` or a configured deploy workflow
per repo. A rollback is a revert PR added to the same task, which then runs the
same cycle.

## Rollout

1. Backend core: schema and migration, stage evaluation, shepherd triggers,
   wake-ups and budget, backfill, `get_task`, deletions. About 2–3 days.
2. Merge: shared readiness, auto-merge preference and override, merge ordering,
   `set_task_options`. About 1 day.
3. Takeover and release across dashboard, GitHub, and tools. About 1 day.
4. UI: task panel, sidebar chip, settings toggle. About 1 day.

## Open questions

- On a taken-over PR, should comments from a PR author who is not a registered
  Open SWE user wake the driver thread? Today they are dropped.
- Is 5 the right retry budget, and should it differ for CI versus findings?
- Should a reviewer who is not a registered Open SWE user be assignable? GitHub
  review requests work for them, but they get no Slack DM or dashboard view.
- Should reviewer scoring also skip people who are away (Slack status, calendar),
  and learn from how quickly each person has reviewed before?
- Are weekdays 09:00–18:00 in the assignee's Slack time zone the right working
  hours for the acknowledgement clock, or should they be a per-person setting?
- Should a block that nobody answers escalate (for example, re-notify after a
  day, or notify the workspace channel)?
