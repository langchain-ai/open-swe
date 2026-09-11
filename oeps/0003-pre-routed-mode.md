# OEP-0003: Agent-driven model routing through pre-routed mode

- **Authors:** Ramon Nogueira (`@ramon-langchain`)
- **Status:** Draft
- **Created:** 2026-09-11
- **Discussion:** https://github.com/langchain-ai/open-swe/pull/2667
- **Supersedes:** None

## Summary

Model routing becomes a decision the agent makes and records itself. Every thread starts in
**pre-routed mode**: a read-only turn on the cheapest model profile whose only deliverable is a
call to `exit_pre_routed_mode` naming the profile the thread will run on and the thread's title.
The same decision is folded into `exit_plan_mode` for threads that start in plan mode. Thread
titling moves from a background first-message summarizer to the same tool call.

## Motivation

Adaptive routing today runs a hidden classifier over the first human message before the agent
starts. It has three problems:

- **It routes blind.** The classifier sees prose, not code. "Fix the flaky test" and "fix the
  flaky test that only reproduces under the KVM runner" read alike; the difference is in the
  repository.
- **It is invisible and unaccountable.** The agent does not know which profile it is on or why,
  and nothing in the transcript records the decision. Users cannot see or contest it.
- **It throws its own work away.** The classifier is a separate model call with its own prompt.
  Its input is the request text truncated to 8,000 characters, its output is a bare route, and
  neither enters the thread. The agent's first turn then starts cold and reads the same request
  and the same files the classifier was guessing about.

Thread titling has the same shape: a separate model call over the first message, producing a
title that reflects the request's wording rather than what the work turned out to be.

## Proposal

**Pre-routed mode is the default starting state of every routed thread.** While it is active:

- The model call runs on the `fast` profile.
- Only `exit_pre_routed_mode`, `execute`, `read_file`, `ls`, and `glob` may be called; any other
  tool call is rejected with an error result. The system prompt instructs the agent not to
  mutate anything through `execute`.
- The tool list and system prompt are identical before and after the exit. Pre-routed mode is
  described once in the static prompt, and the agent learns it is over from the exit tool's
  result in the transcript. Hiding tools or swapping prompt sections would invalidate the
  provider's prompt cache on every exit, including the common `fast` to `fast` case.
- The system prompt describes the three profiles (`fast`, `balanced`, `performance`) and the
  signals that raise or lower the required capability, and tells the agent to size the whole
  thread, not the first step.

**`exit_pre_routed_mode(model_route, title)` ends it.** The route and title are persisted in the
thread's stored settings. Later model calls in the run and all later runs on the thread use the
chosen profile with the full tool set. The decision is final for the thread; there is no
mid-thread upgrade.

**The sizing turn is the first turn of the work.** Exiting pre-routed mode appends a tool result
and changes which model handles the next call. Nothing else changes: the message history is not
trimmed, summarized, or forked. Every file the agent read and every command it ran while sizing
the task is already in the context the chosen profile continues from, so the routed model resumes
from an understanding of the repository it would otherwise have had to build for itself. This is
the property that makes an exploratory sizing turn affordable at all, and it is the one the
classifier cannot have: a decision made in a separate call can only be carried forward as a
single word.

**Plan mode carries the same decision.** A thread that starts in plan mode skips pre-routed
mode, runs planning on `performance`, and routes when the plan is approved: `approve_plan`
becomes `exit_plan_mode(model_route, title)`. Plan-mode threads are titled when the plan is
first published via an optional `title` argument on `save_plan`, so the placeholder title does
not persist until approval.

**Titling follows routing.** With routing enabled, the agent-chosen title replaces the background
titler and, on desktop, the branch-name model call. Titles still only replace the seeded
placeholder; a title the user set is never overwritten. Threads whose profile has routing
disabled keep the background titler.

**Defaults and boundaries.**

- Routing stays opt-in per profile (`model_routing_enabled`); this proposal changes how a routed
  thread is routed, not who is routed.
- A thread that answers without ever exiting, for example a greeting or a question answerable
  from context, stays on `fast` for its lifetime. This is intended.
- Stop-summary runs and subagents never enter pre-routed mode.
- Dashboard and Slack plan approval clear plan mode without a route; the next run enters
  pre-routed mode with the approved plan as its input.

### Non-goals

- Changing which models back each profile, or how profiles are configured per team.
- Mid-thread re-routing or per-message routing.
- Routing subagents independently of the parent thread.
- Changing plan mode's read-only rules or approval flow beyond the tool rename and its arguments.

## Security and privacy

No change to trust boundaries or credentials. Pre-routed mode is strictly more restrictive than
a normal turn: fewer tools and an explicit no-mutation instruction. The route and title are
written to thread metadata the agent could already write through existing tools. Titles are
derived from repository content the agent can already read; the existing guard that only
replaces the seeded placeholder title is preserved.

## Alternatives

- **Keep the classifier, feed it more context.** Running the classifier after a research turn
  would improve accuracy but keeps a second model call per thread and leaves the decision
  invisible to the agent and the transcript.
- **Route on every human message.** Re-sizing each follow-up matches how plan mode resets but
  makes cost unpredictable and lets a thread thrash between profiles mid-task. A single sticky
  decision is easier to reason about and to explain.
- **Allow mid-thread upgrades.** Keeping the exit tool available lets the agent escalate when a
  task turns out harder than expected. It also invites the cheap model to defer the decision
  indefinitely. Rejected for now; can be revisited with data on how often the initial route is
  wrong.
- **Exit tool only, no exploration.** One model call, no repository reads. Cheaper, but reproduces
  the classifier's core weakness of routing from prose alone.
- **Hide disallowed tools while pre-routed.** Matches how plan mode restricts tools, but the
  tool list is part of the cached prompt prefix, so every exit would re-read the whole prefix.
  Rejecting calls keeps the same guarantee with a stable prefix.

## Costs

Against the classifier's single small call, a sizing turn is expensive in absolute terms: on
open-ended tasks the agent has been observed spending 30 to 56 model calls and up to 1.8M input
tokens before it routes. The prompt asks it to read only enough to size the work and to exit on
the higher profile when torn rather than read further, which is guidance, not a bound.

The comparison is not "one call against thirty", because the thirty are inherited. What is
genuinely spent is the difference between what the agent read to *decide* and what it needed to
*finish* — reads that serve only the routing decision. On a well-scoped task that difference is
near zero. On an open-ended one it is not, and nothing in this proposal bounds it.

Two smaller regressions come with it. The decision is no longer guaranteed to happen: an agent
that never calls the exit tool stays on `fast` with the restricted tool set, though any
disallowed call returns an error naming the exit tool, so the failure is loud rather than
silent. And routing quality now depends on the model backing the `fast` profile, which makes
every routing decision for every thread.

## Unresolved questions

- How to bound the sizing turn. The leading option is a hard cap in the middleware: after N tool
  calls while pre-routed, collapse the tool list to `exit_pre_routed_mode` alone so the next
  model call has to decide. This invalidates the cached prefix at the cap, which is acceptable
  because the runs that reach it are the expensive ones. N is unknown; the eval's
  `tool_calls_before_exit` distribution should set it.
- Whether dashboard and Slack plan approval should route the thread directly instead of sending
  it through pre-routed mode with the approved plan as input.
- Whether the `title` argument on `save_plan` should be required in plan mode rather than
  optional.
