# Deploy-time sandbox `prepare` callback

**Upstream as-is.** Python `define_sandbox(prepare=...)`, modelled on [eve's environment `prepare`](https://eve.dev/docs/sandbox#prepare-a-reusable-environment).

## Usage

```python
# sandbox/__init__.py
from pathlib import Path

from managed_deepagents import define_sandbox


async def prepare(sandbox):
    tools = Path("sandbox/tools")
    uploads = await sandbox.aupload_files(
        [(f"/opt/agent-tools/{path.name}", path.read_bytes()) for path in tools.glob("*.py")]
    )
    if failed := [upload.path for upload in uploads if upload.error]:
        raise RuntimeError(f"upload failed: {failed}")
    result = await sandbox.aexecute("pip install websockets && python3 /opt/agent-tools/browser_cli.py --check")
    if result.exit_code != 0:
        raise RuntimeError(result.output)


sandbox = define_sandbox(prepare=prepare)
```

`mda deploy` (and the credential-backed `mda dev`) creates the bake builder sandbox, runs `sandbox/setup.sh` if present, then runs `prepare` **on the deploy machine**, and captures the builder as the recipe snapshot every new sandbox starts from. The callback may be sync or async, runs from the project root with the deploy machine's environment, and receives the same `LangSmithSandbox` backend agents use at runtime (`execute`, `upload_files`, `write`, their `a*` forms, …). Like eve's example, it must check results itself: `execute` returns a nonzero `exit_code` and `upload_files` reports per-file errors rather than raising. An exception or a 15-minute timeout fails the deploy before the recipe is recorded.

The callback can read anything on the deploy machine, but the recipe digest covers only the project files under `sandbox/` (the callback's code included), so keep what it ships there: change one and the next deploy bakes a new snapshot; leave them alone and it reuses the ready one. Projects without `prepare` keep their digests. Existing live sandboxes are not rewritten.

The callback runs under the project's `.venv` interpreter when present, otherwise the interpreter the `mda` Python launcher ran from (`MDA_PYTHON`), otherwise `python3`; it needs `managed-deepagents` importable and everything `sandbox/__init__.py` imports.

## What changed

- `packages/pypi`: `define_sandbox(prepare=...)` stores the callback on `SandboxDefinition` (outside the create options); `_sandbox_prepare.py` imports the project's `sandbox`, opens the builder with the LangSmith sandbox SDK, and calls it; the launcher exports `MDA_PYTHON`.
- `crates/mda`: compile detects `prepare` (`python_extract/sandbox_prepare.py`) and records its module and `sandbox/` inputs digest in `sandbox-recipe.json`; `RecipeSpec::digest` now owns the recipe identity for compile and bake; `bake.rs` runs `SandboxPrepare` after `setup.sh`, passing the client's key, workspace, and sandbox endpoint (`Client::sandbox_sdk_env`).

**Why:** agent-owned helpers such as a CDP `browser_cli.py` should ship with an ordinary versioned deploy. Building and publishing a custom Docker image needs infrastructure approval in some enterprises, and embedding files in `setup.sh` does not scale past one script.

**Upstream:** adopt `prepare` in MDA, with the TypeScript `defineSandbox` counterpart this prototype lacks, and drop this change.
