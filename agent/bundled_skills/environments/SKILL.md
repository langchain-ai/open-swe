---
name: environments
description: How Open SWE environments work and how to change them — create one, edit or fork an existing one, start from scratch, what setup_script and update_script are for, why a nightly refresh failed, where the build logs are, and how to read a rebuild in progress. Read this whenever someone asks about environments, snapshots, sandbox images, or a refresh, whether or not this is an admin thread.
---

# Environments

An environment is two things: a **record** (name, prompt, repos, sizing, optional scripts) and a published **sandbox image**, `<prefix>-environment-<slug>:latest`. Runs in that environment boot from the image and get the prompt appended to their system prompt. The environment named `default` is what every run uses unless the thread picked another one.

Only workspace admins can change environments, and only from an admin thread. Anyone can read them on **Workspace settings → Environments**. If the asker is not an admin, tell them what would need to happen and who can do it; do not try the tools.

## The one rule: build here, publish from here

You do not write an environment as a script and hope. You build it in the admin thread's own sandbox with ordinary tools — `execute`, the file tools — and when it works you call `publish_environment`. That captures **this sandbox** as the image and writes the record only after the capture succeeded. Nothing is half-written on failure, and the environment is usable the moment the call returns.

Everything on the sandbox filesystem is captured, so provision it fully and leave no tokens, credentials, or proxy secrets on disk.

## Which image to start from

The image you publish is whatever you are sitting on plus your changes, so start the admin thread on the right one:

| You want to… | Start the admin thread… | Then |
|---|---|---|
| **Edit** an environment | in that environment (composer → Environment picker) | change it, `publish_environment` under the **same** name |
| **Fork** one into a new environment | in the parent | change what differs, `publish_environment` under a **new** name |
| Build **from scratch** | in no environment, or `sandbox_reset` with the base `snapshot_id` | provision everything, publish |

Already in a thread and need a different image? `sandbox_reset(snapshot_id=…)` with an id from `list_environments`, or `sandbox_reset(snapshot="<prefix>-environment-<slug>:latest")`. The old sandbox is detached, not deleted.

## `setup_script` — the reproducibility contract

Optional. It is **not** the build; you already did the build by hand. It is the record of how to reproduce the image from the base snapshot, and when present a nightly cron replays it on a throwaway sandbox and replaces the image **only if it exits 0**. Anything you did by hand that the script does not do therefore shows up as a *failed refresh* on the Environments page rather than as silent drift. `refresh_environment_start` runs the same check on demand.

Two consequences:

- The script must describe the **whole** build from base, even for a fork. By hand you only did the delta on top of the parent; the nightly does not start from the parent.
- Leaving it off is allowed. The environment then has no nightly check and keeps whatever image was last published.

Write it non-interactive and safe to re-run: start with `set -euo pipefail`; clone the repos; install `rg`, `gh`, toolchains, dependencies; warm caches. Never write a secret to disk — the proxy injects git auth per run.

## `update_script` — keeping a live image fresh

Optional; typically `git pull` plus a dependency sync. Keep it to seconds. It runs in three places:

1. At the end of every full rebuild, so a broken one is caught on a builder.
2. **In a run's own sandbox** when it boots from an image older than an hour, before the first model call, so the run works against fresh checkouts. Never fatal — a failure is logged and the run continues on the image as captured.
3. As an hourly **update refresh** — boot from the current image, run it, capture — enqueued by that same creation, so later runs skip step 2.

## Reading a refresh

A rebuild takes minutes to an hour and runs on a throwaway builder, not the thread's sandbox. `refresh_environment_start` returns `status: "started"` and a `task_id`; it is not done. Read it with `background_task("status", task_id)`, which reports the stage reached — `boot`, `setup`, `update`, `capture` — and, while a script is running, a tail of its live `bash -x` trace. Check in at intervals; do not poll in a loop.

When it fails: the stage that broke is marked `failed` with its exit code, `error` names the script, and `output` is the log. The **previous image stays in place** — runs never drop to the base snapshot because a script broke. Fix the script, `publish_environment` with the new `setup_script`, and run the check again.

Logs are written under `/open-swe/environment/logs/` (`setup.log`, `update.log`) with the scripts beside them, and are captured into the image — so any sandbox booted from an environment can read how its own image was built.

## Snapshot names

The name belongs to the environment, not to whichever sandbox produced the image: every publish and every refresh moves the same `latest` tag. Runs boot from the immutable snapshot id on the record, so a capture mid-run cannot change what a reconnecting sandbox comes back to. Override the name with `snapshot_name` on publish; it must not contain a colon.
