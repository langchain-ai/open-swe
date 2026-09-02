# AGENTS.md

This file applies to all work under `ui/`.

## Package manager

- `ui/` is a package in the repo-root pnpm workspace (`pnpm-workspace.yaml`), orchestrated by Turborepo. The lockfile, `overrides`, and `allowBuilds` live at the root — do not add a lockfile or workspace file here.
- Use **pnpm** for dashboard dependency management and script execution.
- Run UI scripts with `pnpm run <script>` from this directory, or `pnpm --filter open-swe-dashboard run <script>` from the root.
- Install or update UI dependencies with `pnpm install` / `pnpm add` only.
- Do **not** use npm in this directory: no `npm install`, `npm ci`, `npm run`, `npx`, or npm lockfile changes.
- Do **not** use Bun in this directory: no `bun install`, `bun add`, `bun run`, `bunx`, or Bun lockfile changes.
- If a command must use npm or Bun, it belongs outside `ui/` in a subtree that explicitly owns that package-manager configuration and lockfiles.
