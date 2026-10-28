# The CLI's tests leave the npm runtime alone

**Verdict: not upstream as-is.** Upstream ships the npm runtime beside the CLI, where checking its pins is right.

**Changed:** `crates/mda/src/dependencies.rs`: the test comparing the CLI's TypeScript browser pins with `packages/npm/package.json` and `browser-langsmith.ts` keeps only its check that Python deployments run the same Stagehand.

## Why

The CLI's tests run here (`make vendor-check`), and that test reads two npm runtime files at compile time.
Vendoring `packages/npm/package.json` for it would put an npm manifest with about twenty dependencies Open SWE never installs in front of every dependency scanner of this repository, and Open SWE deploys only the Python runtime.

## Upstream

Nothing to ask for. Keep this on every update.
