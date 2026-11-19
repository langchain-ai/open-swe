# Robin rollout checklist

The code changes the visible product name to Robin.
Existing deployments still need these administrator and release checks before rollout.

## External profiles and integrations

- [ ] Update the existing Slack app and bot display names, descriptions, and icons; register `/robin` while keeping `/oswe` on the same webhook
- [ ] Update the existing GitHub App profile and Linear app display names, descriptions, and icons with their owners; retain app IDs, installations, OAuth credentials, and signing secrets
- [ ] Have the GitHub App owner register or rename the existing App as **Robin Agent** (`robin-agent`), verify its actual bot login, and update bot-identity recognition if the login changes; keep App IDs, installations, and historical bot identities compatible.
      The GitHub trigger is `@robin-agent`, never the unrelated `@robin` account; legacy aliases remain supported unless deployment configuration overrides them.
      Slack remains **Robin**
- [ ] Review repository rulesets and branch protection **before** deploying the `Robin Review` check name; replace any required `Open SWE Review` context with `Robin Review`, verify the app source, and confirm a fresh-head check satisfies the rule
- [ ] Update any required monorepo CI contexts that refer to the renamed `Robin:` jobs or `Robin CI Final Results` before merging
- [ ] Check integrations that parse PR attribution; new PRs use `Made by [Robin]`, while historical footers remain valid
- [ ] Disable or uninstall the Claude Code plugin `open-swe@open-swe`, remove the old marketplace (`claude plugin marketplace remove open-swe`), then re-add `langchain-ai/open-swe` to register the `robin` marketplace and install `robin@robin` for MCP tools; remove mirroring hooks pointing to `/dashboard/api/claude-code/hooks` from `~/.claude/settings.json`.
      The previous `/oswe/mcp` endpoint is no longer served; update clients to `/robin/mcp` and sign in again

## Integration-routing cutover

- [ ] Quiesce admin routing writes through the dashboard, API, and agent tools before the routing migration; old and new routing edits are not dual-written
- [ ] Quiesce webhook/event admission and old event-match producers, then drain retained, undelivered webhook matches under the old runtime and its authorization checks before stopping it.
      Include matches whose one-shot subscription was already deleted: inspect `event_match`, not only active subscriptions.
      Actual run receipt must acknowledge delivery; a queued run or another thread turn alone is not evidence of receipt
- [ ] Stop the cutover if any retained, undelivered webhook match lacks `_event_audience`; migration `4b3643ee9665` refuses this state before changing the schema.
      Reconcile delivery under the old runtime before retrying.
      Do not delete matches, mark them delivered without receipt, or synthesize audience metadata from the current thread to bypass the guard.
      Keep admission quiesced through migration and new-runtime startup: the preflight read is not a distributed write fence.
      Historical rows remain retained, and the new runtime intentionally refuses missing audience provenance
- [ ] Pause queue admission and stop and drain old merge-queue schedulers and workers on every replica, reconciling in-flight dispatches and external check/result publication before migration or new-runtime startup; pausing alone does not drain this work
- [ ] Keep old and new queue runners from overlapping; binding-qualified job keys, configuration-generation checks, and native provenance checks protect only work handled by the new runtime, not older binaries or external publication already in flight
- [ ] Verify migrated repository bindings, workflow-start grants, Slack modes, and queue routing before resuming routing writes or queue processing; follow the [merge-queue cutover procedure](MERGE_QUEUE.md#integration-routing-cutover)

## Desktop release and IT

- [ ] Publish signed, notarized `Robin-*` downloads with legacy `Open-SWE-*` aliases; confirm both download names install `Robin.app`
- [ ] Have the external Homebrew tap maintainer update the `open-swe-desktop` cask's app path to `Robin.app` and expose `robin` alongside `oswe`; keep the existing cask identifier until that migration is coordinated
- [ ] Pilot a **signed native macOS upgrade** from the current app: confirm replacement of `Open SWE.app`, existing login and Keychain decryption, backend configuration, projects, local tasks, window state, single-instance behavior, Universal Links, downloaded UI updates, and both CLI commands
- [ ] Have IT update managed-install paths, allowlists, inventory display names, and support documentation; retain the `com.langchain.openswe` bundle ID, legacy Electron profiles, Keychain identity, and `~/.open-swe` configuration so users keep their data
- [ ] Publish the native desktop release as well as backend/UI changes; Linux or unsigned development checks do not establish signed macOS upgrade behavior

## Compatibility boundaries

- [ ] Leave existing dashboard/deployment URLs, OAuth callbacks, webhook URLs, and Universal Link origins untouched
- [ ] Keep repository/export paths (`langchain-ai/langchainplus/open-swe/` and `langchain-ai/open-swe`), package names, environment variables, graph IDs, database schemas, connection slugs, and protocol names unchanged
- [ ] Keep the genuine legacy bot email when displaying the Robin Git collaborator; preserve the injected coauthor trailer exactly rather than constructing a new identity
- [ ] Keep the original announcement link as historical context; do not rename external accounts or repoint URLs as part of a display-name change

Implementation remains in `langchain-ai/langchainplus` under `open-swe/`.
Current release and MDA deployment workflows live in the monorepo's root `.github/workflows/`; the public repository remains the export destination.
