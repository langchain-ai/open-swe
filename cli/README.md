```bash
  ░██████    ░██████   ░███████      ░███
 ░██   ░██  ░██   ░██  ░██   ░██    ░██░██
░██        ░██     ░██ ░██    ░██  ░██  ░██
░██        ░██     ░██ ░██    ░██ ░█████████
░██        ░██     ░██ ░██    ░██ ░██    ░██
 ░██   ░██  ░██   ░██  ░██   ░██  ░██    ░██
  ░██████    ░██████   ░███████   ░██    ░██
```

Open SWE CLI: a terminal client for the Open SWE agent. Built with Ink (TUI), LangGraph, and TypeScript.

The CLI runs in two modes:

- **Local mode** — runs the agent loop in-process against a model provider of your choice. Useful for offline iteration and quick local edits. No deployment required.
- **Cloud mode** — talks to a deployed Open SWE backend over HTTPS. Same backend that Slack, Linear, and GitHub webhooks use, so a CLI-started run shows up next to runs from any other surface and can be resumed from any of them.

See [`DESIGN.md`](DESIGN.md) for the full design.

## Setup

1. Install Bun: `curl -fsSL https://bun.sh/install | bash`
2. Clone the repo and `cd cli`
3. Install deps: `bun install`

## Cloud mode

Cloud mode requires a running Open SWE deployment with `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `ALLOWED_GITHUB_ORG`, `CLI_SESSION_SECRET`, and `CLI_PUBLIC_BASE_URL` configured (see the repo-root [`INSTALLATION.md`](../INSTALLATION.md)).

- `openswe login` — start GitHub OAuth flow and store a session token locally.
- `openswe runs` — list your runs across all surfaces (CLI, Slack, Linear, GitHub).
- `openswe attach <thread-id>` — stream events for an existing run and send follow-up messages.
- `openswe new --cloud "<prompt>" --repo owner/name --branch <branch>` — start a new cloud run.

## Local mode

Run the interactive CLI with `bun run start`. On first run, it prompts for an API key for whatever model provider you've configured.

Common slash commands: `/help`, `/status`, `/model`, `/review`, `/reset`, `/clear`, `/quit`. Press `Tab` to switch modes (agent/plan), `Esc` to interrupt or exit.

## Development

- `bun run dev` — TypeScript watch mode for local iteration.
- `bun run build` — emits ESM output to `dist/`.
- `bun run start` — runs the compiled CLI from `dist/`.
- `bun run test` — runs vitest.

## Global install from source

```bash
bun run build
bun link
```

Then `openswe` is available globally from your shell.

## Structure

- `index.ts` — executable entry point, forwards to `src/coda.tsx`.
- `src/app/` — agent runner, command executor, Zustand store.
- `src/agent/` — LangGraph agent, tools, prompts (local mode).
- `src/tui/` — Ink-based UI components and hooks.
- `src/lib/` — framework-agnostic helpers.

See `AGENTS.md` for contributor guidelines and `DESIGN.md` for the full CLI design.
