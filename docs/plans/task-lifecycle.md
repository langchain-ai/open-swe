# Task lifecycle

## Decision

A **task** is a new first-class entity above threads and pull requests. It owns
every thread about it (its coordinator thread, subthreads, fix and follow-up
threads, and each PR's reviewer and review scout threads) and every pull request it produces or adopts, across any
number of repositories.

A task has one or more owners (usually one), who are accountable, and zero or
more assignees, who must act next. There are no assignees while the agent is
handling things. The agent assigns people when the task needs them, with an
explicit `assign_task` call and its own judgment, guided by rules and facts the
system supplies: reviewers (never the authors) while it waits for human review,
someone to resolve it when it is `blocked`, and someone to merge when auto-merge
is off. There is no auto-assignment. `blocked` is its own state, separate from
waiting for review.

The task has one state. Its pull requests only contribute conditions (CI failing,
review needed, conflict) that the task's state is derived from.

An always-on, model-free **shepherd** carries the task from its first PR to merge.
On each relevant webhook it rereads the affected PR's conditions from GitHub,
recomputes the task's state, records it in the task's audit trail, wakes the task's coordinator
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
its consumer), and with subthreads ([async subagents plan](https://github.com/langchain-ai/open-swe/pull/3074))
one task spans threads too. Merge and readiness have to be decided for the whole
task.

## Model (`agent/tasks/models.py`)

```
task
  id                uuid7
  title             text
  goal              text      what done looks like, from create_task
  coordinator_thread_id text not null; the one thread that coordinates the task
  auto_merge        bool null null = use the owners' preferences
  state             text      derived, cached (see Task state)
  created_at, closed_at
  close_reason      completed | abandoned | null
  last_progress_at  timestamptz drives staleness
  stale_at          timestamptz null

task_thread                    permanent; rows are never deleted or moved
  thread_id         text PK   zero or one task per thread: no row = a free thread;
                              the primary key makes more than one impossible
  task_id           uuid
  role              coordinator | sub | fix | followup | reviewer | review_scout
                              exactly one coordinator row, matching task.coordinator_thread_id
  pull_request_id   uuid null the PR a reviewer or review_scout thread belongs to

task_pull_request
  pull_request_id   uuid PK   -> pull_request; a PR belongs to at most one task, forever
  task_id           uuid
  source            opened | takeover
  released_at       timestamptz null set when handed back; the row stays
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

task_assignee                  who must act now; no active rows = the agent has it
  id                uuid7
  task_id           uuid
  user_id           uuid      -> user
  reason            review | blocked | merge | manual
  pull_request_id   uuid null the PR it concerns
  decided_by_kind   agent | human
  decided_by_user   uuid null the registered user, for a human decision
  decided_by_thread text null the agent thread, for an agent decision
  source            tool | claim | panel | slack_card | pr_comment | github_request
  rationale         text null the agent's one-line reason
  requested_by      uuid null the person whose request the agent acted on
  assigned_at       timestamptz
  due_at            timestamptz null acknowledgement deadline, review only
  acknowledged_at   timestamptz null
  ended_at          timestamptz null
  end_reason        reviewed | passed | timed_out | removed | done | stale | null
  unique active (task_id, user_id, reason, pull_request_id) where ended_at is null

task_reviewer_suggestion       input to the agent's reviewer choice
  id                uuid7
  task_id           uuid
  pull_request_id   uuid
  user_id           uuid      -> user; the suggested reviewer
  suggested_by      uuid      -> user
  source            chat | slack
  strength          preferred | required
  created_at        timestamptz
  outcome           assigned | declined | null

task_audit                     append-only; see Audit trail
  id, task_id, at, actor_kind, actor_user_id null, actor_thread_id null,
  actor_run_id null, actor_github_login null, via, action, pull_request_id null,
  before jsonb, after jsonb, reason text, refs jsonb

task_wakeup                    dedupe + retry budget
  pull_request_id, head_sha, reason   unique
  task_id, created_at
```

### Membership

- The agent creates a task explicitly with `create_task(title, goal, owners)` as soon as
  it decides the work will change code, before any PR exists. A takeover also
  creates a task when the PR is not being added to an existing one.
- Every task has exactly one **coordinator thread**, stored as
  `task.coordinator_thread_id`. It is the thread the shepherd wakes, the one that
  makes the agent's assignment decisions, and the one that answers for the task.
  It is the thread that called `create_task` (or, for a takeover, the thread the
  takeover started or ran in), and it never changes.
- **Membership is permanent.** A thread belongs to at most one task, and once it
  joins one it stays in that task forever, including after the task completes,
  is abandoned, or goes stale. A thread already in a task cannot call
  `create_task`; new work starts in a new thread.
- The thread's task is "in scope" for its runs. Subthreads, fix threads, reviewer
  and scout threads are members, never the coordinator.
- **Concierge threads are never in a task.** They coordinate at a higher level
  with their own tool, `start_task_thread`, instead of `create_task`, which is not
  loaded there. It creates a new thread, creates the task with that thread as
  coordinator, and dispatches the first run with the concierge's instructions.
  The concierge's requester and any named participants become the new thread's
  participants. The concierge then reads and steers tasks through `get_task` and
  messages to their coordinator threads. A concierge thread is never a
  coordinator, never a member, and never assigned. Other surfaces that should
  behave this way get the same treatment by thread kind.
- A subthread (`parent_thread_id` set) joins its parent's task as `sub`.
- A thread started from the dashboard PR actions (`agent/threads/pr_fixes.py`)
  joins the PR's task as `fix` when one exists.
- Each PR's reviewer thread (`reviewer_thread_id`) and review scout thread
  (`review_scout_thread_id`) join the task as `reviewer` and `review_scout` when
  the PR joins, or when they are created later for a PR already in a task. Like
  every member, they stay in the task, including after the PR is released.
  Reviewer and scout threads for PRs outside any task are unchanged.
- The shepherd never wakes a `reviewer` or `review_scout` thread; the existing
  review triggers keep driving them. Owning them puts every thread about the task
  (building, reviewing, and explaining it) in one place: the task panel, the
  audit trail, cost, and `get_task`.
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
- `PullRequest.agent_thread_id` callers move to the task's coordinator thread, so
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

| State | When | Who is usually assigned (by the agent) |
|---|---|---|
| `completed` / `abandoned` | the task is closed | nobody |
| `stale` | no progress for 2 working days (see [Stale](#stale)) | nobody |
| `blocked` | the task has an open block | whoever can resolve it |
| `merging` | an auto-merge sequence is running | nobody |
| `in_progress` | the task has no PRs yet, or any PR has an agent-actionable condition | nobody, the agent has it |
| `in_review` | any PR has `review_needed` | its reviewers |
| `waiting_on_checks` | any PR has `ci_pending` or `bot_review_pending` | nobody |
| `ready_to_merge` | every open PR is mergeable | whoever merges, when auto-merge is off |
| `merged` | every PR is merged or closed, and at least one merged | nobody |

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
`state`, and an audit row when either changes.

### Wake-ups

When shepherd-actionable conditions (`conflict`, `ci_failed`, `bot_findings`)
appear on a PR's head SHA, the shepherd inserts `task_wakeup(pr, sha, reason)`
and, only if the insert succeeded, dispatches one run to the coordinator thread
through the normal queue. The prompt
(`agent/resources/prompts/runs/task-wakeup.md.jinja`) names the repo, PR, head
SHA, and every actionable condition at once, so a PR failing CI with open
findings gets one wake-up, not two.

Human comments and reviews keep flowing through the existing untagged-comment
path, which already carries the text. The shepherd only records the resulting
conditions.

Retry budget: one shared budget per PR across CI failures, conflicts, and
findings. After 5 wake-ups on one PR without any of its actionable conditions
clearing (a new head SHA alone is not progress), the task is blocked with reason
`retry_budget_exhausted` on that PR.

### Blocked

`blocked` means something went wrong or is missing, and the task cannot move
until a person resolves it. It is separate from `in_review`, which is
the expected hand-off to reviewers.

A block records `reason`, a one-paragraph `ask` addressed to the person, and the
PR it applies to (or none for a task-wide block). The agent assigns who resolves
it: directly through `request_human`, or on the wake-up that follows a block the
shepherd entered (see [Assignment](#assignment)). Reasons:

| Reason | Entered by |
|---|---|
| `agent_request` | The agent, via `request_human`, when it needs a decision, access, or information it cannot get: an ambiguous review comment, conflicting reviewer asks, a secret, a product call |
| `retry_budget_exhausted` | The shepherd, after 5 wake-ups without progress |
| `merge_failed` | The shepherd, when an auto-merge sequence stops partway |
| `permission_denied` | The shepherd, when a push, merge, or check read fails on GitHub permissions (a fork without `maintainer_can_modify`, a protected branch, a missing App permission) |
| `no_reviewer` | The agent, via `request_human`, when a PR needs review and no candidate fits (see [Assignment](#assignment)) |

While blocked, the shepherd keeps evaluating and recording the audit trail but sends
no wake-ups for the blocked PR (all PRs, for a task-wide block), and auto-merge
does not run.

A block clears when:

- an assignee or an owner replies in the task's thread, Slack thread, or on
  the PR. The reply wakes the coordinator thread with the block's `ask` and the answer
- the person clicks "Unblock" in the task panel, optionally with a note
- the cause goes away on its own (for example, a human pushes a fix that turns
  CI green, or a permission is granted), which the next evaluation detects. The
  shepherd never clears an `agent_request` block on its own

Clearing a block resets the PR's retry budget, ends the `blocked` assignments,
and is recorded in the audit trail.

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

- no reevaluation from webhooks or the sweep, no wake-ups (so no assignment
  decisions), nudges, or notifications
- all assignments are removed and the task's GitHub review requests are withdrawn
- the owners get one notification that it went stale, with a Resume link
- it still owns its threads and PRs

Only an owner can bring it back, explicitly: the Resume button in the task panel,
`@open-swe resume` on one of its PRs, or asking the agent in the thread, which
calls `resume_task` and is rejected unless the requester is an owner. Resuming
reevaluates every PR, resets retry budgets, restarts the 2-day clock, and wakes
the agent to make review assignments afresh. An owner can also abandon it
instead.

### Assignment

An assignment is always a decision, made either by the agent or by a person.
There is no auto-assignment: the shepherd never picks anyone. It supplies the
facts, wakes the agent when a decision is due, and keeps the bookkeeping (GitHub
review requests, acknowledgement deadlines, ending an assignment once its job is
done).

- **Agent decisions** are one tool call, `assign_task`, made with the agent's own
  judgment.
- **Human decisions** are explicit actions by a registered Open SWE user: "Take
  it" on an open review call, an assign control in the task panel or on a Slack
  card, `@open-swe assign @login` on a PR, or a review request made on GitHub.
  The same action from anyone who is not a registered user fails and changes
  nothing. A GitHub review request from an unregistered requester is ignored,
  and the ignore is recorded in the audit trail.
- **A human decision supersedes any agent decision.** It can replace or remove an
  agent's assignment. The agent cannot replace or remove a human's assignment;
  `assign_task` returns an error naming who decided. On a human-decided
  assignment, a missed acknowledgement deadline gets a nudge and a note to the
  person who decided, never a replacement.
- Both kinds go through the same function and the same rules below, and record
  `decided_by` (the agent's thread, or the user).

A task has zero or more assignees: the people who must act next. With none, the
agent has it. Owners change only when someone edits them; assignees come and go.
Each assignment has a reason (`review`, `blocked`, `merge`, `manual`), so one
person can be assigned twice for different things (to review one PR and to
unblock another).

The task's **authors** are every owner, every PR author, and whoever took a PR
over.

#### When the agent is asked to decide

The shepherd wakes the coordinator thread with an assignment wake-up at these moments.
They are deduped through `task_wakeup` and do not count toward the retry budget.

| Moment | Decision asked for |
|---|---|
| A PR has no agent-actionable or automation conditions left (CI green, Open SWE review done, nothing to fix) and lacks the approvals it needs | post an open review call, or assign someone directly |
| An open review call went unclaimed for 2 working hours | who reviews it |
| A reviewer passed, or missed the acknowledgement deadline | who reviews instead |
| GitHub requested a team on a PR (for example a `CODEOWNERS` auto-request) | which one member to request instead |
| The shepherd entered a block (`retry_budget_exhausted`, `merge_failed`, `permission_denied`) | who resolves it, and the `ask` |
| The task reached `ready_to_merge` with auto-merge off | who merges |
| An owner resumed a stale task | review assignments afresh |

The agent can also assign at any other time, typically because a person asked for
it in conversation.

#### Open review calls

Teams already self-select reviews by posting PR links in a channel. The agent can
do the same instead of assigning: `post_review_call(pull_request)` posts a card to
the repo's review channel (a per-repo setting) with the PR title, a one-line
summary, size, repo, author, and any requirement ("needs a code owner of
`agent/tasks/`"). This generalizes the expedited review card
(`agent/expedited_review/card.py`, `slack.py`), which already posts, updates, and
removes channel cards.

- "Take it" makes the clicker the reviewer. It is a human decision, so it passes
  the same rules (registered user, not an author, a code owner when the card
  requires one) and fails for anyone else. Taking a review counts as
  acknowledging it and counts toward the taker's review load.
- The card updates in place: "Sam is reviewing", then approved, then removed from
  the channel. "Release" by the taker puts it back up.
- Unclaimed after 2 working hours (in the owners' working hours), the agent gets
  a wake-up and assigns someone directly.
- The agent skips the channel when a `required` suggestion names the reviewer, or
  when no review channel is configured.
- The channel is the team's view of what is available to review.

#### Suggested reviewers

Authors will often suggest a reviewer in conversation ("maybe Alex should review
this"). A suggestion is input to the agent's decision, not an assignment; the
explicit actions listed above are decisions, not suggestions. The agent records a
suggestion with `suggest_reviewer`. It is stored in `task_reviewer_suggestion`
with who suggested whom for which PR, and shows up in `review_candidates` as
`suggested_by`.

Each suggestion has a strength:

- `preferred`: the default. The agent weighs it against the suggested person's
  review load and the alternatives, and honours it unless that person is clearly
  more loaded than comparable candidates. When it declines, it assigns someone
  else and tells the suggester why in the thread or Slack thread, for example "Alex has 4 open reviews and
  took 3 today; asked Sam, who also owns `agent/tasks/`".
- `required`: the suggester has said this specific person is the right reviewer
  ("this really needs Alex", "Alex has to look at this"). The agent infers the
  strength from the wording and records it. It assigns that person regardless of
  load. It does not assign them only when a rule in `assign_task` forbids it, or
  when they passed on this PR. When the person is away or outside working hours,
  it still assigns them and tells the suggester when they are likely to pick it
  up. A missed acknowledgement deadline on a `required` reviewer gets a nudge and
  a note to the suggester instead of a replacement; the agent reassigns only if
  the suggester agrees.

#### Rules every assignment must pass

These apply to agent and human decisions alike. Violations return an error that
names the rule.

- Individuals only, never a team.
- Registered Open SWE users only, since only they can get the Slack DM and
  acknowledge.
- Reviewers have write access to the repo and are not authors.
- A review slot has one assignee at a time; reassigning a slot replaces its
  assignee.
- Someone who passed or timed out on a PR's review cannot be assigned to review
  that PR again.
- The decider is the agent or a registered Open SWE user.
- The agent cannot replace or remove a human-decided assignment.
- Every agent decision carries a one-line `rationale`, shown to the assignee and
  in the audit trail, for example "code owner of `agent/tasks/`, 14 commits to
  these files in 90 days, 2 open reviews".

#### Judgment the agent applies

These go in the assignment wake-up prompt and the `assign_task` description
(under `agent/resources/prompts/`). They are guidance, not code:

- Pick reviewers from `review_candidates`, never from GitHub's suggestions or team
  auto-assignment.
- One reviewer by default. Add more only when branch protection requires more
  approvals or code owner review needs another person; prefer one person who
  covers several owned paths.
- Prefer code owners and people who recently changed or reviewed the files.
- Weigh review load heavily: both what someone holds now and what they took in
  their last working day. Spread reviews rather than piling onto the obvious
  expert.
- Honour `preferred` suggestions unless the person is clearly more loaded than
  comparable candidates; say why when declining. Assign `required` suggestions
  regardless of load.
- Prefer people inside their working hours now; do not pick anyone marked away.
- After changes were requested and fixed, usually re-request the same reviewers.
- For a block, assign whoever can actually answer the `ask`: normally the owners,
  the PR author for a taken-over PR, or a person a reviewer pointed to.
- For a merge, normally the owners.
- When no candidate fits, call `request_human` to the owners with reason
  `no_reviewer` and say why.

#### Reviewer candidates (`review_candidates`, `agent/tasks/reviewers.py`)

For a PR, the tool returns its review requirements (required approval count,
whether code owner review is required, owned paths and their owners) and a list of
candidates with raw signals. It never scores or picks.

Candidates are registered Open SWE users with write access who are not authors,
drawn from `CODEOWNERS` entries for the changed paths (teams expanded to their
members) and from recent committers and reviewers of the changed files.

| Signal | Source |
|---|---|
| Code ownership | `CODEOWNERS` paths they own among the changed files, with lines changed under each |
| Change history | their commits to the changed files in the last 180 days, from the GitHub commits API per path (top 20 files by lines changed) |
| Review history | their reviews on earlier PRs touching the same files |
| Load | the counts from [Review load](#review-load) |
| Suggested | who suggested them for this PR, if anyone |
| Working hours | whether they are inside working hours now, and when their next working hour starts |
| Away | Slack presence or status marking them away (vacation, out sick, away), read live at call time |
| History on this PR | whether they already reviewed, passed, or timed out |

History signals are cached per repo and path for an hour. Presence is not cached.

#### Review load

Review load is tracked per person and is the main input when choosing between
candidates or deciding on a suggestion. For each registered user:

| Count | Source |
|---|---|
| `active_reviews` | Open SWE `review` assignments not yet ended |
| `awaiting_ack` | of those, not yet acknowledged |
| `assigned_last_working_day` | `review` assignments made to them during their most recent working day, including ones already ended, using their stored working hours |
| `github_review_requests` | open review requests on GitHub across the installation's orgs that are not Open SWE assignments, from GitHub search (`is:open is:pr review-requested:<login>`), cached for 10 minutes |

The first three are one query over `task_assignee`, exposed as a `reviewer_load`
view, so they are exact and cost nothing to read. `review_candidates` returns all
four for every candidate, plus the median across the candidate list for
comparison, and `assign_task` returns the assignee's updated load so the agent
sees the effect of its call.

#### Acknowledgement

Each review slot has exactly one assignee at a time, who must acknowledge within
**2 working hours**.

- Acknowledging means clicking "Start review" in the Slack DM or task panel, or
  any review activity on the PR: a review comment, a submitted review, or opening
  the PR's Open SWE review page.
- "Pass" in the Slack DM or task panel ends the assignment immediately.
- On pass or a missed deadline on an agent-decided assignment, the assignment
  ends, its GitHub review request is withdrawn, the person is told, and the agent
  gets a wake-up to choose someone else. On a human-decided assignment, a missed
  deadline only nudges the reviewer and tells the person who decided; a pass ends
  it and tells that person, who decides what happens next.
- The 2-hour clock counts only the assignee's working hours (see
  [Working hours](#working-hours)), so a review assigned overnight does not time
  out before anyone is awake.
- Once acknowledged, the deadline no longer applies. If no review arrives within
  1 working day, the shepherd nudges the reviewer once and tells the owners.

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
- `review_candidates` reports each candidate's working-hours status from the
  stored values. When the agent assigns someone outside working hours, the clock
  starts at their next working hour.
- `due_at` is computed once by `assign_task` from the stored values; the sweep
  only compares `due_at`.

#### Bookkeeping

The shepherd keeps assignments consistent with GitHub without choosing anyone:

- A `review` assignment requests the review on GitHub; ending it withdraws the
  request.
- A review request a registered user makes directly on GitHub is recorded as
  that user's decision. A team request triggers the "which member" wake-up above.
- An assignment ends when its job is done: the reviewer submits a review on the
  current head (approval or changes requested), the block clears, or the PR
  merges. A stale task ends all of them. Ending is not reassigning; if another
  decision is needed, the agent gets a wake-up.
- Assigning a person while the agent is working does not stop the agent.

Every change is recorded in the audit trail. Each new assignee is notified once, through a
Slack DM and a banner in the task panel carrying the reason and the agent's
rationale: the block's `ask`, "review <PR>", or "ready to merge". A blocked task
also gets a comment on the affected PR.

### Merge

When the task state becomes `ready_to_merge`:

- `auto_merge = task.auto_merge ?? all(o.preferences.auto_merge_shepherded_prs for o in owners)`.
  With several owners and no override, every owner must have opted in.
- Off: post "ready to merge" once to the task thread, Slack thread, and each PR,
  and wake the agent to assign who merges.
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
release it, then evaluates immediately. The coordinator thread's first wake-up
includes the full current state (CI, findings, unresolved threads) so it can
catch up in one run.

A takeover is refused, with the reason, when:

- the PR's author is not a registered Open SWE user. Their comments and reviews
  could never reach the agent, so the PR could not be shepherded through review
- the PR comes from a fork and `maintainer_can_modify` is false

### Release

`@open-swe release`, a dashboard button, or `release_pull_request`. The PR author
and any task owner can always release. Release sets `released_at`: the shepherd
stops tracking the PR and ends its assignments, but the PR stays in the task's
record, like its threads. It is recorded in the audit trail and posts a PR
comment. A released PR can only be taken over again into the same task.

## Settings

- `UserPreferences.auto_merge_shepherded_prs: bool = False`
  (`agent/users/preferences.py`), in the dashboard settings.
- Per-task override `task.auto_merge`, set from the task panel toggle, from chat
  ("merge when ready", "don't auto-merge") through `set_task_options`, or with
  `@open-swe automerge on|off` on any of the task's PRs.
- Taking a PR over into a new task makes the person who took it over the owner;
  adding it to an existing task leaves that task's owners unchanged.
- Per-repo review channel for open review calls, in the repository settings.

## Audit trail

Every task keeps a complete, append-only record of what happened, who or what did
it, and why. This is separate from the webhook delivery log
(`agent/webhooks/event_log.py`), which records raw payloads; the audit trail
records decisions and their effects in domain terms.

All task mutations go through one module (`agent/tasks/audit.py`), which writes
the audit row in the same transaction as the change, so there is no change
without a record. Rows are never updated or deleted, including when a task is
abandoned.

Each row records:

| Field | Meaning |
|---|---|
| `at` | when |
| `actor_kind` | `human`, `agent`, `shepherd`, or `github` (an outside GitHub actor) |
| `actor` | the registered user, the agent thread and run, or the GitHub login |
| `via` | `dashboard`, `slack`, `pr_comment`, `github`, `tool`, `sweep`, `webhook` |
| `action` | one of the actions below |
| `pull_request_id` | the PR concerned, if any |
| `before`, `after` | the relevant values on each side of the change |
| `reason` | the agent's rationale, the block's `ask`, the human's note, or the rule that fired |
| `refs` | related ids: assignment, suggestion, wake-up run, review, commit SHA, webhook delivery |

Actions:

| Group | Actions |
|---|---|
| Lifecycle | task created, closed (completed or abandoned), went stale, resumed |
| State | task state changed (from, to, and the conditions that caused it) |
| People | owner added or removed; assignment made, ended (reviewed, passed, timed out, removed, done), or superseded by a human; review acknowledged; suggestion recorded, honoured, or declined; review call posted, taken, released, or gone unclaimed |
| Work | PR added, taken over, or released; commit pushed; condition appeared or cleared; review submitted; wake-up sent (with the run it started); retry budget exhausted |
| Blocks | block entered (reason and `ask`), answered (by whom, with the answer), cleared |
| Merge | auto-merge changed (by whom), merge started, PR merged, merge failed |
| Rejections | an action refused by a rule (for example an unregistered user's "Take it", or the agent trying to replace a human's assignment), with the rule |

Rejections are recorded because they explain why something did not happen.

`get_task` returns the recent trail. `GET /dashboard/api/tasks/{id}/audit`
returns the whole trail, filterable by action group, actor, and PR, and
exportable as JSON.

## Agent tools (`agent/tools/tasks.py`)

| Tool | Behaviour |
|---|---|
| `create_task` | `title`, `goal`, `owners: [login \| email]` (thread participants only, at least one); binds the calling thread as coordinator, permanently. Fails if the calling thread already belongs to a task. Not loaded in concierge threads |
| `start_task_thread` | Concierge threads only. `title`, `goal`, `owners`, `instructions`, `repos`; creates a new coordinator thread and its task, and dispatches the first run |
| `close_task` | `reason: completed \| abandoned` |
| `get_task` | Task state, PRs with their conditions, recent audit trail |
| `resume_task` | Moves a stale task back into the cycle; only when the requesting participant is an owner |
| `link_pull_request` | Gains `shepherd: bool`; adds the PR to the thread's task |
| `release_pull_request` | Stop shepherding a PR; it stays in the task's record |
| `set_task_options` | `auto_merge: bool \| null`, `merge_after: {pr: [prs]}`, `owners: {add: [login \| email], remove: [login \| email]}` (added owners must be thread participants) |
| `request_human` | `ask`, `assignees: [login]`, `rationale`, `reason: agent_request \| no_reviewer`, `pull_request?`; blocks the task (or one PR), assigns it, and ends the run |
| `review_candidates` | `pull_request`; review requirements and candidates with raw signals, review load, and suggestions (see [Reviewer candidates](#reviewer-candidates-review_candidates-agenttasksreviewerspy)) |
| `post_review_call` | `pull_request`, `summary`; posts an open review call to the repo's review channel |
| `suggest_reviewer` | `pull_request`, `reviewer: login`, `suggested_by: login`, `strength: preferred \| required`; records a suggestion a participant made in conversation |
| `assign_task` | `add: [login]`, `remove: [login]`, `reason: review \| blocked \| merge \| manual`, `pull_request?`, `rationale`; the agent's only way to assign. `review` also requests review on GitHub. Fails on a human-decided assignment |

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
with its linked agent thread as coordinator, then drop the baby-sit crons.
Active watches need no conversion because the backfilled task covers the same PR.
A thread that is mid-change without a PR at deploy time hits the gate on its next
edit and gets the `create_task` error, which is the intended path.

## Later

Task states extend past `merged` without changing the model: `deploying`, `deployed`,
`verified`, driven by GitHub `deployment_status` or a configured deploy workflow
per repo. A rollback is a revert PR added to the same task, which then runs the
same cycle.

