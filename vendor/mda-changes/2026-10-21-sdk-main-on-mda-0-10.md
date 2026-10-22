# SDK `main`'s newer commits on top of `mda/0.10`

**Verdict: upstream as-is.** It is `main`'s own code, kept until `mda/0.10` merges `main`.

**Changed:** what SDK `main` changed in the vendored paths between `mda/0.10`'s branch point (`4d10735a`) and the commit vendored before (`938f0a54`), merged into `mda/0.10`'s head as `git merge` would:

- 0.9.3 (#910): the `managed-deepagents` version, `pydantic<2.15`, and the `oxc`, `uv-pep440`, `ruff` and `ty` pins, with their locks (#799, #914, #915, #918).
- #901: self-hosted runtime mounts and credentials.
- #905: falling back to the deployment's `LANGSMITH_TENANT_ID` (`mda/0.10` already has it).
- #745: failing writes to unmounted memory layers instead of dropping them.
- #919: dropping other package managers' lockfiles from the build.
- #940: suppressing the MCP import beta warning.

## What

`UPSTREAM` records `mda/0.10`'s head, which branched off `main` before these commits. Moving there from `main` with `scripts/vendor.py --branch mda/0.10` merges from where the two meet, so the tree keeps them instead of reverting to `mda/0.10`'s older copies. `--diff` lists them among our changes.

## Why

Open SWE already ran them on `main`; the Linear and GitHub channels on `mda/0.10` shouldn't cost it these fixes.

## Upstream

Merge `main` into `mda/0.10`. The next `--branch mda/0.10` update then finds the same changes on both sides, and this note goes.
