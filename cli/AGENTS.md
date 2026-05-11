# Repository Guidelines

This document summarizes how to work effectively inside the coda CLI codebase. Keep the CLI responsive, testable, and idiomatic to Ink and TypeScript.

## Project Structure & Module Organization
- `index.ts` is the executable entry point and forwards to `src/coda.tsx`.
- `src/coda.tsx` handles initial setup (API key prompt) and starts the Ink renderer.
- `src/app/` contains core application logic:
  - `agent-runner.ts`: Logic for running the LangGraph agent stream.
  - `command-executor.ts`: Handles slash command execution.
  - `store.ts`: Zustand store for global state management.
  - `commands.ts`: Metadata for slash commands.
  - `stream-processor.ts`: Processes and formats agent stream updates for the UI.
- `src/tui/` contains the Ink-based user interface:
  - `App.tsx`: The main UI entry point.
  - `components/`: Presentational components.
  - `hooks/`: Custom React hooks for UI state and logic.
- `src/agent/` contains all agent-related logic:
  - `graph.ts`: LangGraph setup and agent definition.
  - `tools/`: Individual tools the agent can use (filesystem, shell).
  - `prompts.ts`: System prompts for the agent.
- `src/lib/` contains framework-agnostic helpers:
  - `storage.ts`, `logger.ts`, `diff.ts`, `time.ts`, etc.
  - `models.ts`: Defines the available LLM models.
- `src/types/` contains shared TypeScript types.
- `src/evals/` contains agent evaluation logic using LangSmith.
- Test setup sits in `test-setup.ts`; Vitest config resides in `vitest.config.ts`.
- Transpiled JavaScript lands in `dist/`; treat it as a build artifact only.

## Build, Test, and Development Commands
- `bun install` syncs dependencies and keeps `bun.lock` authoritative.
- `bun run build` runs `tsc` then `tsc-alias` and emits ESM output to `dist/`.
- `bun run dev` keeps TypeScript in watch mode for local iteration.
- `bun run start` executes the compiled CLI from `dist/index.js`.
- `bun run test` invokes `vitest run`, excluding evaluation tests.
- `bun run eval` runs only the agent evaluation tests in `src/evals/`.

Path aliases are available in both `tsc` and Vitest via `tsc-alias` and Vite resolve aliases:
- `@app/*` → `src/app/*`
- `@tui/*` → `src/tui/*`
- `@agent/*` → `src/agent/*`
- `@lib/*` → `src/lib/*`
- `@types` → `src/types/index.ts`
- ...and various more specific aliases defined in `tsconfig.json`.

## Coding Style & Naming Conventions
- Stick to strict TypeScript, ES2022 modules, and 2-space indentation.
- Prefer named exports; reserve default exports for CLI entry files only.
- Components and hooks follow React conventions: PascalCase for components, camelCase for functions and variables, UPPER_SNAKE for constants.
- Use Zustand for shared state management.
- Keep terminal output ASCII, wrap messaging for narrow terminals, and document non-obvious UI flows inline with brief comments.

## Testing Guidelines
- Place `*.test.ts` or `*.test.tsx` beside the code they verify (e.g., `src/lib/__tests__/storage.test.ts`).
- Use Vitest with `jsdom` for a testing environment.
- Cover argument parsing, exit codes, and message rendering; update or add snapshots when UI framing changes.
- Ensure new UI surfaces still render within typical terminal widths (80x24) during tests. (We are not adding UI Tests right now due to rapidly changing UI during development)

## LangSmith Integration
- LangSmith is configured for agent tracing and evaluation.
- Environment variables are loaded via dotenv in `index.ts` at startup.
- Required environment variables for tracing:
  ```bash
  LANGSMITH_TRACING=true
  LANGSMITH_API_KEY=your_api_key_here
  LANGSMITH_PROJECT=coda