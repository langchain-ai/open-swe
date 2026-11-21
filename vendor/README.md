# Vendored code

> [!IMPORTANT]
> **Vendoring grants no license.** The code in this directory is copied here only so this repository can build and run it. Neither Open SWE's [MIT License](../LICENSE) nor the code's presence in this repository licenses it to anyone. Any rights to it come only from its owner, on the owner's own terms. A license file inside a vendored directory was copied from upstream with the rest of the code: this repository does not grant it.

## Managed Deep Agents SDK (not vendored)

The Managed Deep Agents SDK is not vendored. `mda/` and the backend install its `managed-deepagents` Python runtime from langchainplus's `sdks/managed-deepagents/packages/pypi` at the commit `[tool.uv.sources]` pins in both `pyproject.toml`s (today on its `mda/0.11` branch, until MDA 0.11 is released), and `make mda-cli` builds the `mda` CLI from that same commit (see the Makefile). Change the SDK upstream, then move both pins to the new commit and relock both projects.

## `langchain-quickjs/`

[langchain-quickjs](https://github.com/langchain-ai/deepagents/tree/main/libs/partners/quickjs), the code mode `mda/` runs (`CodeInterpreterMiddleware`), from `libs/partners/quickjs` of the deepagents repository at the commit in `UPSTREAM`. `mda/` installs it from here as an editable uv path source, and `mda build` bundles it as a local package. It is vendored so Open SWE can change it before upstream does.

`scripts/vendor.py` manages it, in a vendor-branch workflow: our changes are edits in the tree itself, reviewed as ordinary diffs, and `UPSTREAM` records the exact commit the tree comes from (full SHA, then its date, then its branch when that is not `main`).

- `uv run scripts/vendor.py` updates to the head of upstream `main`; `uv run scripts/vendor.py <sha>` updates to that commit; `--branch <branch> [<sha>]` takes another upstream branch's head, or a commit on it. Files we left alone take upstream's new version; into files we changed, it three-way merges upstream's changes (`git merge-file`, base: the recorded commit) and stops with conflict markers where both sides changed the same lines. Then it records the commit in `UPSTREAM` and relocks `mda/`.
- `uv run scripts/vendor.py --pr <n>` carries upstream pull request `<n>` before it reaches `main`, recorded in `langchain-quickjs-upstream-prs`: its number, the head commit carried, then its title.
- `uv run scripts/vendor.py --diff` prints our changes, carried pull requests included: the tree against `UPSTREAM`'s commit.

`DEEPAGENTS_REPO` overrides the upstream, e.g. with a local checkout.

Every change we make there ourselves has a dated note in [`langchain-quickjs-changes/`](langchain-quickjs-changes/README.md): what changed, why Robin needs it, and what upstream needs before we can drop it. A carried pull request needs none: `langchain-quickjs-upstream-prs` names it, and the pull request says the rest.
