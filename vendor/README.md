# Vendored code

> [!IMPORTANT]
> **Vendoring grants no license.** The code in this directory is copied here only so this repository can build and run it. Neither Open SWE's [MIT License](../LICENSE) nor the code's presence in this repository licenses it to anyone. Any rights to it come only from its owner, on the owner's own terms. A license file inside a vendored directory was copied from upstream with the rest of the code: this repository does not grant it.

## `managed-deepagents-sdk/`

The [Managed Deep Agents SDK](https://github.com/langchain-ai/managed-deepagents-sdk) at the commit in `UPSTREAM`: the `mda` CLI's Rust source (`crates/`) and the `managed-deepagents` Python runtime (`packages/pypi/`). `mda/` installs the runtime from here (an editable uv path source), and `make mda-cli` builds the CLI with `cargo` into that runtime's `_binaries/`, where its `mda` command runs it from. `mda build` and `mda deploy` bundle this runtime into the deployment, so what runs is exactly this tree.

Only the SDK's `main` is vendored, and `UPSTREAM` records the exact commit the tree comes from (full SHA, then its date). Our changes are edits in the tree itself, as on a vendor branch: change the SDK by editing its files here, and review the change as an ordinary diff. `scripts/vendor.py` manages the rest:

- `uv run scripts/vendor.py` updates to the head of upstream `main`; `uv run scripts/vendor.py <sha>` updates to that commit, and refuses one that is not on `main`. Files we left alone take upstream's new version. Into files we changed, it three-way merges upstream's changes (`git merge-file`, base: the recorded commit), and stops with conflict markers where both sides changed the same lines. Then it records the commit in `UPSTREAM` and relocks `mda/`.
- `uv run scripts/vendor.py --diff` prints our changes: the tree against `UPSTREAM`'s commit. Once upstream has one of them, the next update drops it from that list.
