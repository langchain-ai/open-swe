# Vendored code

## `managed-deepagents-sdk/`

The [Managed Deep Agents SDK](https://github.com/langchain-ai/managed-deepagents-sdk) at the commit in `UPSTREAM`: the `mda` CLI's Rust source (`crates/`) and the `managed-deepagents` Python runtime (`packages/pypi/`). `mda/` installs the runtime from here (an editable uv path source), and `make mda-cli` builds the CLI with `cargo` into that runtime's `_binaries/`, where its `mda` command runs it from. `mda build` and `mda deploy` bundle this runtime into the deployment, so what runs is exactly this tree.

Update it with `scripts/vendor_mda_sdk.sh [ref]` (default `main`), then `cd mda && uv lock`. The script replaces the directory and re-applies `patches/managed-deepagents-sdk/*.patch`; change the vendored code by adding a patch there, and delete it once upstream has the change.
