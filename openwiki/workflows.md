---
type: Workflow Guide
title: Coding, review, planning, and learning workflows
description: Source-to-result workflows for Open SWE coding tasks, PR review, plan approvals, repository review-style learning, and follow-up execution.
resource: /agent/webhooks
tags: [open-swe, workflows, slack, linear, github, review]
---
# Coding, review, planning, and learning workflows

## Coding-task workflow

A coding task can originate from a Slack mention/thread, a Linear issue interaction, a GitHub comment, or dashboard chat. The source-specific webhook/route constructs the relevant context and resolves a deterministic thread ID so later messages about the same source can return to the same work. All sources use the [runtime architecture](runtime-architecture.md)’s durable dispatcher, which interrupts and resumes active work for follow-up input.

The coding graph then prepares the agent: identity/model settings are resolved, the thread sandbox is provisioned or reattached, and the prompt incorporates task/source context. The agent is expected to work end-to-end—inspect and modify the target repository, validate its changes, commit/push, open or update a draft PR where permitted, and reply through its source channel. There is intentionally no generic post-run hook that opens a PR on its behalf.

Slack and Linear are collaboration surfaces as well as entrypoints. Middleware and source tools allow progress/status replies and receipt of messages that arrive while the agent is running. GitHub flows additionally support PR comment handling and repository policies. The [integrations and security](integrations-security.md) page explains the identity and authorization gates that make these workflows safe enough to expose.

## Planning and workflow approvals

The main agent has plan-mode tooling (`enter_plan_mode` and `save_plan`), and FastAPI mounts plan routes through `agent/dashboard/plan_api.py`. The dashboard surfaces plan review in agent thread routes, so a user can inspect and respond to a proposed approach rather than treating every request as immediate repository mutation.

Workflow-file pushes take a stricter path: Slack routing contains an explicit owner-approval flow, and FastAPI also mounts `agent/dashboard/workflow_approval_api.py`. This approval workflow **is surfaced by the [dashboard](dashboard.md)** but remains enforced in backend routes/middleware; do not replace it with client-side visibility alone.

## Pull-request review workflow

GitHub PR events can invoke the `reviewer` graph. Review routes gate the event and derive a reviewer-specific deterministic thread. Reviewer preparation gathers the PR metadata and diff, computes the valid changed-line set, reads prior review threads and configured guidelines/style, then starts the review agent.

The reviewer maintains evolving findings through review-only tools (`add_finding`, `update_finding`, `list_findings`, `publish_review`) and only publishes findings that are valid and renderable. Its prompt and pre-processing treat PR text and historical thread content as untrusted. It does not receive the coding graph’s write/PR-authoring tool chain. This separation **depends on the server-side trust boundaries in [integrations and security](integrations-security.md)** and is evaluated by the benchmark described in [operations and quality](operations-quality.md).

## Review-style learning

The `analyzer` graph builds a repository-specific style prompt in two modes:

- **Bootstrap:** inspect historical review signals to establish the initial style.
- **Continual:** refine that style using outcomes from this reviewer’s findings.

Skills under `agent/skills/bootstrap-repo-analysis/` and `agent/skills/continual-learning/` define the modes. Dashboard jobs in `agent/dashboard/review_style_jobs.py` launch analysis, while `agent/dashboard/analyzer_cron.py` supports recurring refinement after bootstrap. The dashboard lets administrators manage the resulting repository style, connecting this workflow to [dashboard](dashboard.md).

## CI auto-fix and scheduled work

GitHub signals such as failed checks or actionable review feedback can feed the project’s CI auto-fix behavior, subject to repository, user/profile, and per-PR opt-ins. The source logic lives around webhook handling and related agent/dashboard modules; it should be changed with high caution because it can create subsequent coding runs on an agent-authored PR.

The `scheduler` graph and dashboard schedules support delayed or recurring work. One-shot wakeup operational cleanup is handled by `scripts/purge_wakeup_crons.py`; deployment/verification guidance belongs in [operations and quality](operations-quality.md).

## Change guidance

- **Add an invocation source:** implement a verified route and handler, create deterministic IDs, pass clear `configurable` context, and dispatch through `dispatch_agent_run`. Add source-focused tests under `tests/webhooks/`, `tests/slack/`, `tests/github/`, or equivalent.
- **Alter prompts or input context:** examine source handler, `agent/prompt.py`, and tool reply behavior together. Inputs from external systems must remain data, not instructions that override system policy.
- **Alter review publication:** update `agent/reviewer.py` with `agent/review/findings.py` and `agent/review/publish.py`; validate targeted reviewer tests and, for quality changes, the reviewer evaluation.
