# AGENTS.md

This file applies to all work under `ui/`.

## Package manager

- Use **Bun** for dashboard dependency management and script execution.
- `bun.lock` is the canonical lockfile for this directory.
- Run UI scripts with `bun run <script>` (for example, `bun run typecheck`, `bun run lint`, `bun run test`, `bun run build`).
- Install or update UI dependencies with `bun install` / `bun add` only.
- Do **not** use npm in this directory: no `npm install`, `npm ci`, `npm run`, `npx`, or npm lockfile changes.
- If a command must use npm, it belongs outside `ui/` in a subtree that explicitly owns npm configuration and lockfiles.
