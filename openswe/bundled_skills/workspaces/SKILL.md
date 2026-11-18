---
name: workspaces
description: How a Robin workspace's sandbox image works and how to change it — create one, edit or fork an existing one, start from scratch, what setup_script and update_script are for, why a nightly refresh failed, where the build logs are, and how to read a rebuild in progress. Read this whenever someone asks about workspaces, snapshots, sandbox images, a refresh, or sandbox commands failing to find preinstalled tools, whether or not this is an admin thread.
---

# Workspaces

A workspace prefers repositories and owns Slack channels and team settings, plus the sandbox image its runs boot from.
Preferring a repository routes its GitHub and Linear events here and preloads it into the image; it does not limit access, since every workspace's sandbox can reach every repository the GitHub App installation can.
That image is two things: a **record** (name, prompt, repos, sizing, optional scripts) and a published **sandbox image**, `<prefix>-environment-<slug>:latest`.
Runs routed to a workspace boot from its image and get its prompt appended to their system prompt.
The workspace named `default` is what a run uses when routing picks no other.

Only workspace admins can change a workspace's image, from an admin thread or through supported writes in a verified sole-writer shared thread.
Shared writes return only acknowledgements; workspace reads and switching to another workspace's image still require a private admin surface.
Anyone can read workspaces on the **Workspaces** page.
If the asker is not an admin, tell them what would need to happen and who can do it; do not try the tools.

## The one rule: build here, publish from here

You do not write a workspace's image as a script and hope.
You build it in the current thread's own sandbox with ordinary tools — `execute`, the file tools — and when it works you call `publish_workspace`.
That captures **this sandbox** as the image and writes the record only after the capture succeeded.
Nothing is half-written on failure, and the image is usable the moment the call returns.

Everything on the sandbox filesystem is captured, so provision it fully and leave no tokens, credentials, or proxy secrets on disk.

## Installed tools must work in later commands

A successful provisioning command does not prove a usable image.
Snapshots keep files, not the shell process that installed them: an `export PATH=...` in `setup_script`, `update_script`, or one `execute` call does not carry into the next call.
Commands are non-interactive; do not rely on `.bashrc` or a login shell to activate tools.

For durable command defaults, use the workspace's existing `create_params.run_config.env_vars` setting when publishing or configuring it.
For example, a root-owned image with mise and Rust installed under `/root` can use:

```json
{
  "run_config": {
    "env_vars": {
      "PATH": "/root/.local/bin:/root/.cargo/bin:/root/.local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    }
  }
}
```

Use paths appropriate to the image's command user, keep its required system paths, and supply a complete literal value: `$PATH`, `$HOME`, and `~` are not shell-expanded in this JSON.
Preserve existing creation settings when changing them; never store credentials here.
Creation settings apply to new sandboxes, not existing ones, and image inheritance does not inherit them.
Read the current settings on a private admin surface before editing, and confirm provisioning changes to `default` first.
Do not recreate a sandbox just to apply them without the user's explicit request.

Before publishing, check tool lookup and versions from a **separate `execute` call** with no inline exports or activation, in the repository where they will run.
If using new creation settings, verify in a disposable sandbox created with those settings; exporting the same PATH in the build command does not test them.
Check representative tools needed by the repository, including its commit hooks, not just package installation exit codes.

When an existing run reports a missing tool, inspect its PATH, repository tool configuration, and documented installation locations first.
If the tool is already installed, use the documented PATH or tool-manager execution context for the command; do not rerun installation or bypass a hook.
A genuinely absent dependency still follows repository rules for setup and approval.

## Which image to start from

The image you publish is whatever you are sitting on plus your changes, so start the admin thread on the right one:

| You want to… | Start the admin thread… | Then |
|---|---|---|
| **Edit** a workspace's image | in that workspace (composer → Workspace picker) | change it, `publish_workspace` under the **same** name |
| **Fork** one into a new workspace | in the parent | change what differs, `publish_workspace` under a **new** name |
| Build **from scratch** | in no workspace, or `recreate_sandbox(source="base")` | provision everything, publish |

Already on a private admin surface and need a different image?
`recreate_sandbox(workspace=<slug>)` with a slug from `list_workspaces`.
The old sandbox is detached, not deleted.

## `setup_script` — the reproducibility contract

Optional.
It is **not** the build; you already did the build by hand.
It is the record of how to reproduce the image from the base snapshot, and when present a nightly cron replays it on a throwaway sandbox and replaces the image **only if it exits 0**.
Anything you did by hand that the script does not do therefore shows up as a *failed refresh* on the Workspaces page rather than as silent drift.
`snapshotctl(action="rebuild")` runs the same check on demand.

Two consequences:

- The script must describe the **whole** build from base, even for a fork.
  By hand you only did the delta on top of the parent; the nightly does not start from the parent.
- Leaving it off is allowed.
  The workspace then has no nightly check and keeps whatever image was last published.

Write it non-interactive and safe to re-run: start with `set -euo pipefail`; clone the preferred repos listed in `OPENSWE_WORKSPACE_REPOS` into `/workspace/<repo>`, where runs expect every checkout (runs clone any other repository on demand); install `rg`, `gh`, toolchains, dependencies; warm caches.
Never write a secret to disk — the proxy injects git auth per run.

## `update_script` — keeping a live image fresh

Optional; typically `git pull` plus a dependency sync.
Keep it to seconds.
It runs in three places:

1. At the end of every full rebuild, so a broken one is caught on a builder.
2. **In a run's own sandbox** when it boots from an image older than an hour, before the first model call, so the run works against fresh checkouts.
   Never fatal — a failure is logged and the run continues on the image as captured.
3. As an hourly **update refresh** — boot from the current image, run it, capture — enqueued by that same creation, so later runs skip step 2.

## Reading a refresh

A rebuild takes minutes to an hour and runs on a throwaway builder, not the thread's sandbox.
`snapshotctl(action="rebuild")` returns `status: "started"` and a `task_id`; it is not done.
Read it with `snapshotctl(action="status")` or `background_task("status", task_id)`, which report the stage reached — `boot`, `setup`, `update`, `capture` — and, while a script is running, a tail of its live `bash -x` trace.
Check in at intervals; do not poll in a loop.

When it fails: the stage that broke is marked `failed` with its exit code, `error` names the script, and `output` is the log.
The **previous image stays in place** — runs never drop to the base snapshot because a script broke.
Fix the script with `snapshotctl(action="edit")`, which needs no sandbox and returns the previous values so the edit can be undone, then rebuild again.
`snapshotctl(action="history")` shows who changed the workspace and when; `stop` cancels a rebuild that is going nowhere.

Logs are written under `/open-swe/environment/logs/` (`setup.log`, `update.log`) with the scripts beside them, and are captured into the image — so any sandbox booted from a workspace's image can read how it was built.

## Snapshot names

The name belongs to the workspace, not to whichever sandbox produced the image: every publish and every refresh moves the same `latest` tag.
Runs boot from the immutable snapshot id on the record, so a capture mid-run cannot change what a reconnecting sandbox comes back to.
Override the name with `snapshot_name` on publish; it must not contain a colon.
