<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="apps/docs/logo/dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="apps/docs/logo/light.svg">
    <img src="apps/docs/logo/dark.svg" alt="Open SWE Logo" width="35%">
  </picture>
</div>

<div align="center">
  <h1>Open SWE</h1>
  <p><strong>The open-source framework for building your org's internal coding agent</strong></p>
</div>

Elite engineering orgs like Stripe, Ramp, and Coinbase are building their own internal coding agents — Slackbots, CLIs, and web apps that meet engineers where they already work. These agents are connected to internal systems with the right context, permissioning, and safety boundaries to operate with minimal human oversight.

Open SWE is the open-source version of this pattern. Built on [LangGraph](https://langchain-ai.github.io/langgraph/) and [Deep Agents](https://github.com/langchain-ai/deepagents), it gives you the same architecture those companies built internally: cloud sandboxes, Slack and Linear invocation, subagent orchestration, and automatic PR creation — ready to customize for your own codebase and workflows.

> [!NOTE]
> 💬 Read the **announcement blog post [here](https://blog.langchain.com/open-swe-an-open-source-framework-for-internal-coding-agents/)**

---

## Architecture

Open SWE makes the same core architectural decisions as the best internal coding agents. Here's how it maps to the patterns described in [this overview](https://x.com/kishan_dahya/status/2028971339974099317) of Stripe's Minions, Ramp's Inspect, and Coinbase's Cloudbot:

### 1. Agent Harness — Composed on Deep Agents

Rather than forking an existing agent or building from scratch, Open SWE **composes** on the [Deep Agents](https://github.com/langchain-ai/deepagents) framework — similar to how Ramp built on top of OpenCode. This gives you an upgrade path (pull in upstream improvements) while letting you customize the orchestration, tools, and middleware for your org.

```python
create_deep_agent(
    model="anthropic:claude-opus-4-6",
    system_prompt=construct_system_prompt(repo_dir, ...),
    tools=[http_request, fetch_url, commit_and_open_pr, linear_comment, slack_thread_reply],
    backend=sandbox_backend,
    middleware=[ToolErrorMiddleware(), check_message_queue_before_model, ...],
)
```

### 2. Sandbox — Isolated Cloud Environments

Every task runs in its own **isolated cloud sandbox** — a remote Linux environment with full shell access. The repo is cloned in, the agent gets full permissions, and the blast radius of any mistake is fully contained. No production access, no confirmation prompts.

Open SWE supports multiple sandbox providers out of the box — [Modal](https://modal.com/), [Daytona](https://www.daytona.io/), [Runloop](https://www.runloop.ai/), and [LangSmith](https://smith.langchain.com/) — and you can plug in your own. See the [Customization Guide](CUSTOMIZATION.md#1-sandbox) for details.

This follows the principle all three companies converge on: **isolate first, then give full permissions inside the boundary.**

- Each thread gets a persistent sandbox (reused across follow-up messages)
- Sandboxes auto-recreate if they become unreachable
- Multiple tasks run in parallel — each in its own sandbox, no queuing

### 3. Tools — Curated, Not Accumulated

Stripe's key insight: *tool curation matters more than tool quantity.* Open SWE follows this principle with a small, focused toolset:

| Tool | Purpose |
|---|---|
| `execute` | Shell commands in the sandbox |
| `fetch_url` | Fetch web pages as markdown |
| `http_request` | API calls (GET, POST, etc.) |
| `commit_and_open_pr` | Git commit + open a GitHub draft PR |
| `linear_comment` | Post updates to Linear tickets |
| `slack_thread_reply` | Reply in Slack threads |

Plus the built-in Deep Agents tools: `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`, `write_todos`, and `task` (subagent spawning).

### 4. Context Engineering — AGENTS.md + Source Context

Open SWE gathers context from two sources:

- **`AGENTS.md`** — If the repo contains an `AGENTS.md` file at the root, it's read from the sandbox and injected into the system prompt. This is your repo-level equivalent of Stripe's rule files: encoding conventions, testing requirements, and architectural decisions that every agent run should follow.
- **Source context** — The full Linear issue (title, description, comments) or Slack thread history is assembled and passed to the agent, so it starts with rich context rather than discovering everything through tool calls.

### 5. Orchestration — Subagents + Middleware

Open SWE's orchestration has two layers:

**Subagents:** The Deep Agents framework natively supports spawning child agents via the `task` tool. The main agent can fan out independent subtasks to isolated subagents — each with its own middleware stack, todo list, and file operations. This is similar to Ramp's child sessions for parallel work.

**Middleware:** Deterministic middleware hooks run around the agent loop:

- **`check_message_queue_before_model`** — Injects follow-up messages (Linear comments or Slack messages that arrive mid-run) before the next model call. You can message the agent while it's working and it'll pick up your input at its next step.
- **`open_pr_if_needed`** — After-agent safety net that commits and opens a PR if the agent didn't do it itself. This is a lightweight version of Stripe's deterministic nodes — ensuring critical steps happen regardless of LLM behavior.
- **`ToolErrorMiddleware`** — Catches and handles tool errors gracefully.

### 6. Invocation — Slack, Linear, and GitHub

All three companies in the article converge on **Slack as the primary invocation surface**. Open SWE does the same:

- **Slack** — Mention the bot in any thread. Supports `repo:owner/name` syntax to specify which repo to work on. The agent replies in-thread with status updates and PR links.
- **Linear** — Comment `@openswe` on any issue. The agent reads the full issue context, reacts with 👀 to acknowledge, and posts results back as comments.
- **GitHub** — Tag `@openswe` in PR comments on agent-created PRs to have it address review feedback and push fixes to the same branch.

Each invocation creates a deterministic thread ID, so follow-up messages on the same issue or thread route to the same running agent.

### 7. Validation — Prompt-Driven + Safety Nets

The agent is instructed to run linters, formatters, and tests before committing. The `open_pr_if_needed` middleware acts as a backstop — if the agent finishes without opening a PR, the middleware handles it automatically.

This is an area where you can extend Open SWE for your org: add deterministic CI checks, visual verification, or review gates as additional middleware. See the [Customization Guide](CUSTOMIZATION.md#6-middleware) for how.

---

## Comparison

| Decision | Open SWE | Stripe (Minions) | Ramp (Inspect) | Coinbase (Cloudbot) |
|---|---|---|---|---|
| **Harness** | Composed (Deep Agents/LangGraph) | Forked (Goose) | Composed (OpenCode) | Built from scratch |
| **Sandbox** | Pluggable (Modal, Daytona, Runloop, etc.) | AWS EC2 devboxes (pre-warmed) | Modal containers (pre-warmed) | In-house |
| **Tools** | ~15, curated | ~500, curated per-agent | OpenCode SDK + extensions | MCPs + custom Skills |
| **Context** | AGENTS.md + issue/thread | Rule files + pre-hydration | OpenCode built-in | Linear-first + MCPs |
| **Orchestration** | Subagents + middleware | Blueprints (deterministic + agentic) | Sessions + child sessions | Three modes |
| **Invocation** | Slack, Linear, GitHub | Slack + embedded buttons | Slack + web + Chrome extension | Slack-native |
| **Validation** | Prompt-driven + PR safety net | 3-layer (local + CI + 1 retry) | Visual DOM verification | Agent councils + auto-merge |

---

## Features

- **Trigger from Linear, Slack, or GitHub** — mention `@openswe` in a comment to kick off a task
- **Instant acknowledgement** — reacts with 👀 the moment it picks up your message
- **Message it while it's running** — send follow-up messages mid-task and it'll pick them up before its next step
- **Run multiple tasks in parallel** — each task runs in its own isolated cloud sandbox
- **GitHub OAuth built-in** — authenticates with your GitHub account automatically
- **Opens PRs automatically** — commits changes and opens a draft PR when done, linked back to your ticket
- **Subagent support** — the agent can spawn child agents for parallel subtasks

---

## Getting Started

- **[Installation Guide](INSTALLATION.md)** — GitHub App creation, LangSmith, Linear/Slack/GitHub triggers, and production deployment
- **[Customization Guide](CUSTOMIZATION.md)** — swap the sandbox, model, tools, triggers, system prompt, and middleware for your org

## License

MIT
