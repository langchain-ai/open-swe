# Drop the deepagents monorepo's path sources

**Verdict: not upstream as-is.** Upstream builds the package inside the deepagents monorepo, where these paths are right.

**Changed:** `pyproject.toml`.

## What

The `[tool.uv.sources]` entry that installs `deepagents` from `../../deepagents`, and the `[tool.ty.environment]` `extra-paths` pointing at the same directory, are gone.

## Why

Out of the monorepo, `../../deepagents` points nowhere: `uv lock` in `mda/` fails to resolve the vendored package, and `mda build` would copy a source entry that cannot resolve into the deployment. `mda/` pins `deepagents` from PyPI itself.

## Upstream

Nothing to ask for: the sources are right in the monorepo. Keep dropping them on every update.
