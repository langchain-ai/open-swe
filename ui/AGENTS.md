# AGENTS.md

This file applies to all work under `ui/`.

## Package manager

- Use **pnpm** for dashboard dependency management and script execution.
- Run UI scripts with `pnpm run <script>` (for example, `pnpm run typecheck`, `pnpm run lint`, `pnpm run test`, `pnpm run build`).
- Install or update UI dependencies with `pnpm install` / `pnpm add` only.
- Do **not** use npm in this directory: no `npm install`, `npm ci`, `npm run`, `npx`, or npm lockfile changes.
- Do **not** use Bun in this directory: no `bun install`, `bun add`, `bun run`, `bunx`, or Bun lockfile changes.
- If a command must use npm or Bun, it belongs outside `ui/` in a subtree that explicitly owns that package-manager configuration and lockfiles.
