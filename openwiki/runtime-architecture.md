---
type: Runtime Architecture
title: LangGraph runtime and agent assembly
description: How Open SWE composes LangGraph graphs, FastAPI routers, durable dispatch, per-thread sandboxes, and Deep Agents factories for coding, review, chat, and scheduling.
resource: /langgraph.json
tags: [open-swe, langgraph, deepagents, backend, runtime]
---
# LangGraph runtime and agent assembly

## Deployed units

`langgraph.json` is the runtime manifest. It registers five graphs and an HTTP application:

| ID | Entrypoint | Role |
|---|---|---|
| `agent` | `agent.graphs.agent:traced_agent` | Main coding agent. |
| `reviewer` | `agent.graphs.reviewer:traced_reviewer_agent` | Read-only PR reviewer. |
| `analyzer` | `agent.graphs.analyzer:traced_analyzer` | Repository review-style analysis. |
| `chat` | `agent.graphs.chat:traced_chat_agent` | Dashboard chat assistant. |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | Scheduled work. |

The same manifest mounts `agent.webapp:app`, which re-exports the FastAPI app assembled in `agent/api/app.py`. That application mounts dashboard, plan, workflow-approval, GitHub, Linear, Slack, and health routers. It validates sandbox and local-model configuration at startup, and only enables credentialed CORS for explicit `DASHBOARD_ALLOWED_ORIGINS`—wildcard origins are rejected.

## From source event to durable run

Source-specific routes in `agent/webhooks/` verify provider input, enforce source/repository rules, build context, and choose a deterministic thread identifier. The routes hand work to `agent/dispatch.py`, which is the shared LangGraph run contract.

`dispatch_agent_run` creates a run with `durability="sync"` and `multitask_strategy="interrupt"`. Sync durability checkpoints each step; an incoming follow-up interrupts the active run and resumes it with its history and new message rather than relying on a process-local queue. When configured with an externally reachable completion URL and secret, the dispatcher also adds a completion webhook. This dispatch mechanism **carries the user flows described in [workflows](workflows.md)** across Slack, Linear, GitHub, and dashboard entrypoints.

## Coding graph assembly

`agent/server.py:get_agent` builds a fresh Deep Agent for a run. Preparation middleware resolves run identity and configuration, obtains or reconnects the thread sandbox, writes relevant metadata/usage, and renders the system prompt. The graph has built-in Deep Agents filesystem, shell, todo, and subagent capabilities plus Open SWE tools for web/research, collaboration, planning, PR work, and optional server-side integrations.

The meaningful state boundary is per thread rather than per graph object: the agent factory is fresh per run, while sandbox identity and run metadata persist with the LangGraph thread. `ensure_sandbox_for_thread` reuses, pings, reconnects, or recreates the backend; `agent/utils/sandbox_state.py` maintains the process cache while durable metadata carries the sandbox ID. This design **depends on the execution and identity controls in [integrations and security](integrations-security.md)**.

Middleware is intentionally ordered in `get_agent`. It includes input sanitization, model-call limits, tool error handling, subdirectory `AGENTS.md` context, task retry/artifact/PR/workflow protections, GitHub proxy refresh, message/status handling, timeout completion behavior, model fallback, plan-mode restrictions, and provider-message cleanup. Treat changing this order as a behavioral/security change, not formatting.

## Reviewer and analyzer graphs

The reviewer factory in `agent/reviewer.py:get_reviewer_agent` is a different graph with a review-specific prompt and toolset. Preparation fetches PR/diff context, changed lines, existing review threads, repository style, target-repository `AGENTS.md`, organization guidelines, and trace context. Findings are validated against the changed-line set before publication. The reviewer deliberately excludes the coding agent’s commit/push/PR-creation path; its lifecycle is described in [workflows](workflows.md).

`agent/analyzer.py:get_analyzer` runs bootstrap or continual review-style analysis. It layers a virtual `StateBackend` `/skills/` route over the sandbox via `CompositeBackend`, exposing playbooks without writing them into the task repository. Its narrow tools save the review-style prompt and read review outcomes. The resulting style becomes context for future reviewer runs.

## Change guidance

- **Add a graph:** implement an entrypoint under `agent/graphs/` or an appropriate factory, then register it in `langgraph.json`; add router/dispatcher integration only when it has an external trigger.
- **Change coding-agent policy:** review `get_agent`, `agent/prompt.py`, registered tools, and the complete middleware sequence together. Add focused tests in `tests/agent/`, `tests/middleware/`, or `tests/tools/` as appropriate.
- **Change reviewer behavior:** inspect `agent/reviewer.py` alongside `agent/review/` and the benchmark described in [operations and quality](operations-quality.md). Preserve the reviewer’s diff-scoped, read-only boundary.
- **Change thread semantics:** start at `agent/dispatch.py` and webhook thread-ID helpers; disrupting deterministic IDs changes whether follow-ups resume existing work.
