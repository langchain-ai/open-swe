# Trim the pyproject to what Open SWE installs

**Verdict: not upstream as-is.** Upstream builds and tests the package inside the deepagents monorepo, where these entries are right.

**Changed:** `pyproject.toml`.

## What

- The `[tool.uv.sources]` entry that installs `deepagents` from `../../deepagents`, and the `[tool.ty.environment]` `extra-paths` pointing at the same directory, are gone.
- So is `[dependency-groups]`, the package's own test and release tooling.

## Why

- Out of the monorepo, `../../deepagents` points nowhere: `uv lock` in `mda/` fails to resolve the vendored package, and `mda build` would copy a source entry that cannot resolve into the deployment. `mda/` pins `deepagents` from PyPI itself.
- The package's tests are not vendored, so its test group installs nothing here; kept, it only adds packages to `mda/uv.lock` and to dependency scans of this repository.

## Upstream

Nothing to ask for: these entries are right in the monorepo. Keep dropping them on every update.
