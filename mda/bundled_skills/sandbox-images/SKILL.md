---
name: sandbox-images
description: How Robin's global sandbox image, execution defaults, setup/update scripts, and build logs work. Read when someone asks about sandbox images, snapshots, provisioning, refreshes, or commands failing to find preinstalled tools, whether or not this is an admin thread.
---

# Sandbox images and defaults

Robin has one global execution configuration and image.
Admins manage shared configuration under **Defaults** (`/settings`) and repository/Slack bindings under **Integrations** (`/admin/integrations`).
Preloading a repository is neither an access grant nor an integration binding.
The stored immutable snapshot ID determines what a new sandbox boots from.
An image capture does not replace a running thread's sandbox.
Do not infer an existing sandbox's contents from today's saved defaults.

Only authorized admins can change shared execution configuration or images.
Private diagnostics require a private dashboard admin thread or authenticated Slack DM; supported shared writes require a verified sole-writer thread and return acknowledgements only.
If the asker is not an admin, explain what an admin needs to do; do not try the tools.
Never publish an image, recreate a sandbox, or delete images without the corresponding explicit request.

## Configure, build, or publish

Read `read_execution_defaults()` first on a private admin surface and preserve omitted settings.
`configure_execution_defaults` saves instructions, preloads, snapshot IDs, resources, scripts, and provider/proxy configuration; **saving is not a build**.
The Defaults page has no build or update buttons; use the authorized image tools.

- `read_execution_image()` reads the current image and private refresh diagnostics
- `rebuild_execution_image(kind="full")` queues a reproducible build from the configured base using the setup script
- `rebuild_execution_image(kind="update")` queues an update from the current image using the update script
- `publish_execution_image()` queues capture of **this thread's current sandbox**, without taking an arbitrary sandbox ID or changing routing, and never stops the source sandbox
- `snapshotctl(action="status")` and `snapshotctl(action="history")` read private diagnostics and recent image actions; `snapshotctl(action="stop")` stops the current build

An `image-...` task ID means queued work, not completed publication.
Inspect or stop it with `background_task`.
A failed script/capture, changed build definition, or lost queue claim leaves the previous good image in place.

For manual provisioning, build and verify with ordinary file and shell tools in the current sandbox before publishing it.
Confirm the intended shared instructions, configuration, and publication with the user first: global publication changes everyone's future sandbox creation.
Everything on the filesystem is captured; leave no tokens, credentials, or proxy secrets on disk.

## Installed tools must work in later commands

A successful provisioning command does not prove a usable image.
Snapshots keep files, not the shell process that installed them: an `export PATH=...` in a script or one `execute` call does not carry into the next call.
Commands are non-interactive; do not rely on `.bashrc` or a login shell to activate tools.

For durable command defaults, preserve and update the image configuration's `create_params.run_config.env_vars`.
For example, an image that runs as root with mise and Rust under `/root` can use:

```json
{
  "run_config": {
    "env_vars": {
      "PATH": "/root/.local/bin:/root/.cargo/bin:/root/.local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    }
  }
}
```

Use paths appropriate to the command user, retain required system paths, and supply a complete literal value: `$PATH`, `$HOME`, and `~` are not shell-expanded in this JSON.
Read existing provider, mount, federation, proxy, and run configuration privately before editing; do not replace it with an empty object or store credentials in it.
Creation settings apply to new sandboxes, not existing ones.
Do not recreate a sandbox to apply them without the user's explicit request.

Before publishing, check tool lookup and versions from a **separate execute call**, with no inline exports or activation, in the repository where they will run.
If new creation settings need verification, a disposable sandbox also requires appropriate authorization; exporting the same PATH in the build command does not test them.
Check representative repository tools and commit hooks, not just installation exit codes.

For a missing tool, inspect PATH, repository configuration, and documented installation locations first.
If installed, use the documented PATH or tool-manager context rather than reinstalling or bypassing hooks.
Genuinely missing dependencies still follow repository setup and approval rules.

## Scripts and refreshes

`setup_script` describes the **whole** reproducible build from the configured base snapshot, not a delta on a parent image.
When present, a nightly full build runs it on a throwaway builder and publishes only after success.
With no setup script, there is no nightly full build; saving configuration still does not provision anything.
Write non-interactive, repeatable scripts starting with `set -euo pipefail`.
Clone preloads listed in `OPENSWE_REPOS` under `/workspace/<repo>`; install required tools, dependencies, and caches without writing secrets to disk.
`OPENSWE_WORKSPACE_REPOS` remains an alias only for already-stored scripts; use `OPENSWE_REPOS` in new scripts.

`update_script` is optional, typically a fast Git/dependency sync.
It runs after a full build, and stale new boots can trigger an hourly update refresh from the current image.
Boot-triggered background refresh failures are logged without interrupting the run; its sandbox keeps the captured image.
New boots use the capture timestamp matching their pinned image, not a newer image's timestamp.
Refreshes do not retrofit existing sandboxes or grant repository permissions.

Builds run on throwaway builders, not the thread's sandbox; manual publication captures the requested source without stopping it.
Status reports the reached stage, such as boot, setup, update, or capture, and script output.
Use background-task completion rather than a polling loop.
On failure, inspect the script, exit code, and output; fix the saved configuration and request a new build.
Logs and scripts under `/open-swe/environment/logs/` (`setup.log`, `update.log`) are captured into images.

## Recreate only when requested

`recreate_sandbox(source="defaults")` boots the current global image and creation settings; `defaults` is the default source.
For an empty sandbox, use `recreate_sandbox(source="base")` only when explicitly requested.
The old sandbox is preserved but becomes inaccessible from the thread after the handoff.
No source preserves the thread's current files, worktree state, or running processes in its replacement.

## Snapshot names

Names identify images, not source sandboxes.
Publications and refreshes move a latest tag, but new runs resolve the stored immutable snapshot ID.
Configure `snapshot_name` without a colon; do not paste `:latest` into that field or treat the tag as proof of the exact stored snapshot ID.
