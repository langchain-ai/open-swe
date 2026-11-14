# Robin rollout checklist

The code changes the visible product name to Robin.
Existing deployments still need these administrator and release checks before rollout.

## External profiles and integrations

- [ ] Update the existing Slack app and bot display names, descriptions, and icons; register `/robin` while keeping `/oswe` on the same webhook
- [ ] Update the existing GitHub App profile and Linear app display names, descriptions, and icons with their owners; retain app IDs, installations, OAuth credentials, and signing secrets
- [ ] Have the GitHub App owner register or rename the existing App as **Robin Agent** (`robin-agent`), verify its actual bot login, and update bot-identity recognition if the login changes; keep App IDs, installations, and historical bot identities compatible.
      The GitHub trigger is `@robin-agent`, never the unrelated `@robin` account; legacy aliases remain supported unless an environment overrides them.
      Slack remains **Robin**
- [ ] Review repository rulesets and branch protection **before** deploying the `Robin Review` check name; replace any required `Open SWE Review` context with `Robin Review`, verify the app source, and confirm a fresh-head check satisfies the rule
- [ ] Update any required monorepo CI contexts that refer to the renamed `Robin:` jobs or `Robin CI Final Results` before merging
- [ ] Check integrations that parse PR attribution; new PRs use `Made by [Robin]`, while historical footers remain valid
- [ ] Disable or uninstall the Claude Code plugin `open-swe@open-swe`, remove the old marketplace (`claude plugin marketplace remove open-swe`), then re-add `langchain-ai/open-swe` to register the `robin` marketplace and install `robin@robin`; use `/robin:connect` and `/robin:thread`, and avoid duplicate hooks or MCP tools.
      The previous `/oswe/mcp` endpoint is no longer served; update clients to `/robin/mcp` and sign in again

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
