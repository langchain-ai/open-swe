# Sandbox workspace seeds

**Upstream as-is.** Prototype of Eve-style workspace seeds for deploy-time snapshots, without a custom Docker image or registry push.

## Usage

Put the files to bake into the project's `sandbox/workspace/` directory:

```text
sandbox/
  __init__.py
  setup.sh
  workspace/
    browser_cli.py
    helpers/
      cdp.py
```

Keep the existing `sandbox = define_sandbox(...)` declaration (`sandbox/index.ts` for TypeScript). `mda deploy` and the credential-backed `mda dev` bake copy these files to `/workspace/browser_cli.py` and `/workspace/helpers/cdp.py` before running the optional `sandbox/setup.sh`. Setup can reference the absolute `/workspace/...` paths to install dependencies or move files elsewhere. Files alone are enough to trigger a bake; factory agents can omit the static sandbox declaration as with setup-only recipes.

The existing snapshot capture, readiness checks, cleanup, and base-image selection remain unchanged. New sandboxes inherit the files; existing live sandboxes are not overwritten. File paths, contents, and executable bits participate in the recipe digest, so changing, renaming, or deleting a file produces a new snapshot. Projects without seeds keep their existing digests.

## Implementation

`compile/sandbox_workspace.rs` builds a deterministic tar.gz from the seed tree and prepends its base64 extraction to the setup script. `compile/sandbox_recipe.rs` and `deploy/mod.rs` use that same composed script for both hashing and baking; `compile/mod.rs` exports the shared reader. This reuses the current sandbox execute path and its failure handling rather than introducing callbacks or a second upload protocol.

The prototype caps uncompressed content at 10 MiB, rejects symlinks and special files, and uses project discovery exclusions (including `.env`, `.env.*`, `.git`, caches, virtualenvs, `build`, and `dist`). Include only files intended for every sandbox: arbitrary credentials under other filenames are not detected. The builder needs `bash`, `base64`, and `tar` with gzip support. Empty directories are not seeded. It does not implement a Python preparation callback or warm running Chrome processes; it seeds the filesystem captured by the existing snapshot API.

**Why:** small agent-owned scripts such as `browser_cli.py` should ship with the agent's normal deploy rather than requiring an infrastructure-owned image release.

**Upstream:** adopt workspace seeds in MDA's compile/bake contract and document them in its public sandbox guide; then drop this vendored change. A native binary upload path would avoid the base64 execute-request overhead before expanding beyond the prototype's size limit.
