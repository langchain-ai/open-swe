<div align="center">
  <a href="https://github.com/langchain-ai/open-swe">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="static/dark.svg">
      <source media="(prefers-color-scheme: light)" srcset="static/light.svg">
      <img alt="Open SWE Logo" src="static/dark.svg" width="35%">
    </picture>
  </a>
</div>

<div align="center">
  <h3>Open-source framework for building your org's internal coding agent.</h3>
</div>

<div align="center">
  <a href="https://opensource.org/licenses/MIT" target="_blank"><img src="https://img.shields.io/github/license/langchain-ai/open-swe" alt="License"></a>
  <a href="https://github.com/langchain-ai/open-swe/stargazers" target="_blank"><img src="https://img.shields.io/github/stars/langchain-ai/open-swe" alt="GitHub Stars"></a>
  <a href="https://github.com/langchain-ai/langgraph" target="_blank"><img src="https://img.shields.io/badge/Built%20on-LangGraph-blue" alt="Built on LangGraph"></a>
  <a href="https://github.com/langchain-ai/deepagents" target="_blank"><img src="https://img.shields.io/badge/Built%20on-Deep%20Agents-blue" alt="Built on Deep Agents"></a>
  <a href="https://x.com/langchain" target="_blank"><img src="https://img.shields.io/twitter/url/https/twitter.com/langchain.svg?style=social&label=Follow%20%40LangChain" alt="Twitter / X"></a>
</div>

<br>

Elite engineering orgs like Stripe, Ramp, and Coinbase are building their own internal coding agents — Slackbots, CLIs, and web apps that meet engineers where they already work. These agents are connected to internal systems with the right context, permissioning, and safety boundaries to operate with minimal human oversight.

Open SWE is the open-source version of this pattern. Built on [LangGraph](https://langchain-ai.github.io/langgraph/) and [Deep Agents](https://github.com/langchain-ai/deepagents), it gives you the same architecture those companies built internally: cloud sandboxes, Slack and Linear invocation, subagent orchestration, and automatic PR creation — ready to customize for your own codebase and workflows.

> [!NOTE]
> 💬 Read the **announcement blog post [here](https://blog.langchain.com/open-swe-an-open-source-framework-for-internal-coding-agents/)**

---

## Start Here

This repo includes a standalone operator workflow built around [`local_fix_agent.py`](./local_fix_agent.py).

### What Do I Do?

Most operators only need two commands:

```bash
fixit pytest tests/test_x.py -q
./scripts/fixpublish.sh
```

Use the first command to make and validate a focused fix. Use the second command to run the required finalizer.

The normal flow is:

```text
fix or edit
-> validate
-> finalize
-> update docs if needed
-> publish
-> verify PR mergeability
```

Important rule:

- a passing validation command is not completion
- the run is only complete after the finalizer runs

### What Is Happening?

The system is doing one thing at a time:

- `fixit ...`
  reproduces the problem, edits the repo, and validates the current state
- `./scripts/fixpublish.sh`
  confirms validation state, updates docs if needed, aligns the branch with its base branch when safe, publishes, and verifies PR mergeability

### Why Did It Do That?

The tool separates validation from finalization on purpose:

- validation proves a repo state
- finalization decides whether that validated state is publishable
- docs updates, branch alignment, and PR mergeability checks happen in finalization so they stay in one safety-gated path

### How Is It Implemented?

The canonical finalizer is:

```bash
./scripts/fixpublish.sh
```

That command is responsible for:

- ensuring a commit-linked validation record exists
- checking meaningful changes
- updating docs if they drift
- rerunning validation if the repo state changes
- aligning the branch with its base branch when safe
- publishing
- checking PR mergeability

If you intentionally want to stop after validation and skip finalization, use `--no-finalize`. That is treated as incomplete, not successful.

If you want the operator workflow first, read these next:

- [Runbook](./docs/RUNBOOK.md)
- [Troubleshooting](./docs/TROUBLESHOOTING.md)

If you want architecture and implementation details, continue below.

## Architecture And Deep Internals

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

## Local Fix Agent

`local_fix_agent.py` is the operator-facing repair and publish tool in this repo.

### What Do I Do?

Use this tool when you want a narrow repair loop and a single required finalizer:

- run a focused validation command
- let the agent edit the repo
- finalize through [`./scripts/fixpublish.sh`](./scripts/fixpublish.sh)

Most common commands:

```bash
fixit pytest tests/test_x.py -q
./scripts/fixpublish.sh
python local_fix_agent.py --interactive
```

### What Is Happening?

Think about the system in three parts:

- Codex:
  reads the repo, makes changes, runs validation, and should always run the finalizer after a successful edit
- the agent:
  owns validation records, docs updates, branch alignment, publish, and PR mergeability checks
- the operator:
  chooses the validation target, reviews the output, and handles truly ambiguous blocked states

### Common Tasks

Fix and validate locally:

```bash
fixit pytest tests/test_x.py -q
```

Fix remotely over SSH:

```bash
fixit --target edge-01 --repo /srv/app "pytest tests/test_x.py -q"
```

Finalize and publish the current validated repo state:

```bash
./scripts/fixpublish.sh
```

Publish the current repo state directly:

```bash
./scripts/publishcurrent.sh
```

Explain the current resolved context without running:

```bash
python local_fix_agent.py --last --explain-only
```

Import a script into the private pattern repo:

```bash
python local_fix_agent.py --script /path/to/example.py --add-to-training
```

Import a repo or folder of scripts into the private pattern repo:

```bash
python local_fix_agent.py --import-pattern-repo /path/to/scripts
python local_fix_agent.py --import-pattern-repo /path/to/repo/tools --pattern-include 'jobs/*.py' --pattern-exclude 'jobs/legacy_*'
```

Inspect learned patterns:

```bash
python local_fix_agent.py --list-patterns
python local_fix_agent.py --list-patterns --filter-state curated_trusted
```

Use the interactive terminal app when you want a guided front-end instead of remembering flags:

```bash
python local_fix_agent.py --interactive
```

Install global user-level launchers when you want the tool available from anywhere on the system:

```bash
./scripts/install_launchers.sh
```

That installs:

- `fixapp` -> `python local_fix_agent.py --interactive`
- `fixpublish` -> `./scripts/fixpublish.sh`
- `fixit` -> `python local_fix_agent.py`

The interactive app is the top-level front-end for the whole system. The main menu and backend routing are in place, and the fully guided workflows now include `Fix or validate a script`, `Create a new script`, `Publish current repo state`, `Import a script into training`, and `Work with a config file`.

The operator UX now has two consistent interactive modes:

- `guided`
  - asks the normal workflow questions
  - keeps advanced options hidden unless requested
- `quick`
  - uses the default answers for the common path
  - still shows a confirmation summary before running

The interactive app presents one top-level menu for:

- fix or validate a script
- create a new script
- publish current repo state
- publish last validated run
- import a script into training
- work with a config file
- inspect learned patterns
- manage patterns
- probe API / M3U8 endpoint
- sync/repair repo conflicts
- settings / advanced options

The `Fix or validate a script` workflow is the primary day-to-day interactive path. It now guides the operator through:

- repo path and script path
- fix-and-validate vs validate-only mode
- validation command choice: auto-detect, remembered default, or custom
- learned pattern source choice
- optional advanced flags
- optional probing when the script looks network-dependent

Before running, it shows a confirmation summary and the equivalent backend command(s). After the run it prints an operator-facing result summary with the validation result, validation command used, patterns/probing context, and a short plain-English `what_happened` line.

The `Publish current repo state` workflow is now also fully guided. It helps the operator review:

- changed files that would be published
- staged vs unstaged state
- whether a validation record exists and still matches the current commit
- whether revalidation is likely to run
- whether auto-stage can safely resolve the current working tree
- whether high-confidence safe blockers can be remediated automatically
- whether publish would block before running the finalizer

It still delegates the real work to the canonical finalizer wrapper, so the safety guarantees stay in one backend path. If publish blocks, the interactive summary explains whether the blocker came from unstaged files, stale validation, or another publish safety check.

When publish blocks on unstaged files, the agent now uses the same centralized classification logic to explain each blocker in operator terms:

- what the file probably is
- why it blocked publish
- whether it should be staged, ignored, left alone, or removed
- exact commands for the safest next action

That includes artifact-style files, internal state files, and safe publishable files that only need `git add`.

The finalizer now also attempts blocker remediation before it gives up. By default it will:

- auto-stage safe publishable paths
- ignore internal state files as non-blocking
- auto-remove high-confidence temporary artifact files when they are clearly junk output

It still does not auto-resolve ambiguous code, config, or unknown data files. Disable blocker remediation with `--no-auto-remediate-blockers`.

The `Import a script into training` workflow is now the guided ingestion path for learned patterns. It supports:

- local file, SSH, or HTTP/HTTPS acquisition
- sanitization before learning so secrets and environment-specific values are redacted
- validation and optional repair before promotion
- trust selection between `trusted` and `experimental`
- classification with confidence scoring before final promotion

The workflow keeps trusted promotion gated. If the imported script is low-confidence, limited-validation, or still failing after repair, the operator is shown a clear summary and can downgrade to experimental trust instead of silently promoting unsafe content.

The backend learning system also supports repo/folder import with `--import-pattern-repo`. That mode scans a local repo or subtree, imports each candidate file through the same sanitize/validate/repair rules, preserves per-file provenance, and learns collection-level conventions such as shared helper structure, naming style, validation style, and recurring network/proxy/auth patterns. Collection imports are grouped under `imports/...` inside the private pattern repo, and the final summary reports candidate counts, promoted counts, blocked counts, repo-level patterns added, and pattern-memory delta.

The `Create a new script` workflow is now the builder path for the system. It asks for the script purpose, output path, an optional domain hint, pattern source, optional bounded probing for network-dependent tasks, and a validation plan. If generation validation fails, it can hand the generated file into the existing fix/validate backend for a repair pass. After a successful generate-and-validate run, it can optionally hand off to the canonical publish flow.

The generation pipeline now plans before it writes. It combines:

- task intent
- trusted learned patterns from file-level and repo-level sources
- optional live API or M3U8 probe evidence when the task is network-dependent
- a selected validation plan
- a generation confidence score

For local-only scripts, generation stays lightweight and pattern-driven. For network-dependent scripts, the tool can use bounded probe findings to shape JSON parsing, playlist branching, proxy/auth handling, redirects, and safe validation choices without hardcoding secrets into the generated file.

The `Work with a config file` workflow is the config-maintenance path. It supports `nginx`, generic reverse proxy configs, `php.ini`, and PHP-FPM pool configs. The workflow can validate, clean up, compare, generate, or align a config file, and it only keeps edits when the selected validation command succeeds. Default validation commands include `nginx -t -c <path>`, `php-fpm -t`, and `php -n -c <path> -m`, with custom validation commands available when the environment needs something more specific. It does not reload or restart services by default.

Across workflows, the interactive app now uses the same shape:

1. `when_to_use`
2. minimal prompts
3. confirmation summary
4. command preview
5. `Run`, `Back`, or `Cancel`
6. result block
7. `what_happened`

If `~/.local/bin` is not already on `PATH`, the installer prints the exact commands to add it for the current shell and to persist it in `~/.bashrc` or `~/.zshrc`.

### Why Did It Do That?

The workflow is intentionally split:

- validation proves a specific commit or repo state
- finalization decides whether that state should publish, noop, or block
- docs handling, base-branch alignment, and PR mergeability checks happen in the finalizer so they stay in the same safety path

### Key Concepts

- Validation record:
  a persisted record that a specific commit was validated successfully
- Finalizer:
  the canonical post-validation step, implemented by [`./scripts/fixpublish.sh`](./scripts/fixpublish.sh)
- Meaningful changes:
  code, docs, tests, scripts, and behavior-relevant config changes; known local state files are ignored
- Pattern repo:
  the private local training repo used for script-pattern learning
- Promotion state:
  `candidate`, `curated_experimental`, `curated_trusted`
- Trust level:
  `experimental` or `trusted`
- Blocked:
  the tool stopped because continuing automatically would be unsafe or too ambiguous

### Safety Rules

- validation success is not completion
- before the repair loop starts, a pre-task git check requires a clean working tree
- the pre-task git check fetches `origin` and `upstream` when those remotes exist
- the current branch is merged with `origin/<current-branch>` first so fork commits stay authoritative
- then `upstream/<default-branch>` is integrated into the current branch with merge semantics, never a hard reset
- if sync creates conflicts, the run stops and reports the conflicting files instead of discarding work
- the pre-task git check never force-pushes automatically
- finalization is required
- `--no-finalize` is an explicit opt-out and leaves the run incomplete
- the finalizer creates or reuses a commit-linked validation record
- docs updates happen inside finalization
- publish decisions use meaningful-change detection
- safe publishable files may be auto-staged during finalization; use `--no-auto-stage` to require fully manual staging
- staging decisions are classified per file as `code`, `test`, `docs`, `config`, `script`, `state`, `generated`, `artifact`, or `unknown`; use `--explain-staging` to print the full reasoning
- live API and HLS/M3U8 probes are available when endpoint truth matters; probe only for network-dependent scripts or debugging, not on every run
- the publish branch is aligned with its base branch before publish when safe
- PR mergeability is checked again after publish as a safety net
- learning uses trust-gated pattern sources; raw candidates do not become trusted automatically

### How Is It Implemented?

The implementation details live lower in this file and in the dedicated docs:

- [Runbook](./docs/RUNBOOK.md) for the normal workflow
- [Troubleshooting](./docs/TROUBLESHOOTING.md) for blocked states and recovery
- [Operator Guide](./docs/README.md) for broader CLI and workflow detail
- [Remote Mode](./docs/REMOTE_MODE.md) for SSH-backed execution
- [`scripts/fixpublish.sh`](./scripts/fixpublish.sh) for the canonical finalizer
- [`scripts/publishcurrent.sh`](./scripts/publishcurrent.sh) for direct publish-current mode

### Where To Go Next

- [Runbook](./docs/RUNBOOK.md) for the normal workflow
- [Troubleshooting](./docs/TROUBLESHOOTING.md) for blocked states and recovery
- [Operator Guide](./docs/README.md) for broader CLI and workflow detail
- [Remote Mode](./docs/REMOTE_MODE.md) for SSH-backed execution
- [`scripts/fixpublish.sh`](./scripts/fixpublish.sh) for the canonical finalizer
- [`scripts/publishcurrent.sh`](./scripts/publishcurrent.sh) for direct publish-current mode

## License

MIT

<!-- fix-agent-prepublish-docs:start -->
## Pre-Publish Docs Gate

Before a real publish, the agent now runs a documentation impact check.
If code or operator-facing behavior changed and docs are stale, it updates the tracked docs before publish, reruns validation, and only then continues with push/PR work.
Current docs refresh policy: `patch` when docs drift is detected.
<!-- fix-agent-prepublish-docs:end -->
