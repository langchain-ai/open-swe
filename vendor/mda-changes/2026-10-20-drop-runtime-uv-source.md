# Drop the runtime's uv source from vendored builds

**Verdict: upstream as-is.** A bug fix any project that installs `managed-deepagents` from a path or git source needs.

**Changed:** `crates/mda/src/compile/manifest.rs` (`rewrite_py_runtime_dep`), with a test in `crates/mda/src/compile/tests.rs`.

## What

When `mda build`, `mda dev` or `mda deploy` swaps the project's `managed-deepagents` dependency for the runtime it bundles, it now also removes that package's entry from the generated `pyproject.toml`'s `[tool.uv.sources]`.

## Why

`mda/` installs the vendored runtime through `[tool.uv.sources]` (`path = "../vendor/managed-deepagents-sdk/packages/pypi"`). The build copies `pyproject.toml` into `.mda/build`, where that relative path no longer points at the runtime. uv prefers a source entry over the bundled copy, so `mda dev` fails to resolve the project.

## Upstream

The edit as it stands: the CLI drops the runtime's own source entry whenever it substitutes the bundled runtime.
