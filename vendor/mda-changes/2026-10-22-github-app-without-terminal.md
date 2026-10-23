# `mda deploy`: bring an existing GitHub App without a terminal

**Verdict: upstream as-is.**

**Changed:** `crates/mda/src/deploy/github_channel/mod.rs` and its `tests.rs`, `crates/mda/src/deploy/channel_app.rs` (`env_app_notice`) and `crates/mda/src/deploy/helpers.rs` (`RESERVED_ENV_VARS`).

## What

The GitHub half of langchain-ai/managed-deepagents-sdk#998. `MDA_GITHUB_APP_ID`, `MDA_GITHUB_APP_PRIVATE_KEY_FILE`, `MDA_GITHUB_APP_INSTALLATION_ID` and `MDA_GITHUB_WEBHOOK_SECRET` bring an existing App into Agent Auth, with or without a terminal, and deploy checks them as a group before uploading anything. All four are reserved: they never become deployment secrets.

## Why

The GitHub channel of every `open-swe-mda-*` deployment uses that environment's own Open SWE GitHub App, whose webhook stays at the backend. Without a terminal, deploy could only create a new App, so `deploy_mda.yml` and the preview workflow failed at preflight for any deployment without the App yet. The workflows now pass it from per-environment secrets.

## Upstream

Drop this once #998 is on the branch we vendor. It is stacked on Slack and Linear channel work (#997, #995, #994 and below) that Open SWE doesn't need yet, so carrying it with `scripts/vendor.py --pr 998` would bring that stack too; this edit takes only its GitHub files.
