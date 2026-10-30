# Agent Workspace Desktop

Agent Workspace is a separate macOS app, not an Open SWE setting or experiment.
It always connects to `https://openswe.langchain.dev` and signs in with LangSmith.
There is no backend URL prompt, backend override, or environment switcher.
The existing Open SWE app keeps its configurable backend, GitHub login, coding UI, CLI integration, and release process.

## Identity and storage

- App name: **Agent Workspace**
- Bundle identifier: `com.langchain.agentworkspace`
- Packaged Electron profile: `~/Library/Application Support/Agent Workspace`
- Source-launch profile: `~/Library/Application Support/Agent Workspace Development`
- Update cache: `agent-workspace-desktop-updater`

Projects, cached UI, browser cookies, local threads, and the single-instance lock belong to that profile.
Agent Workspace never reads or migrates `~/.open-swe/config.json`, and never shares its session with the `oswe` CLI.
It does not bundle or install `oswe`.
Open SWE and Agent Workspace can run side by side without replacing each other's app or signing each other out.

The same immutable desktop UI archives serve both apps.
The main process exposes the fixed build variant as `window.openSweDesktop.appVariant`, either `"open-swe"` or `"agent-workspace"`.
The shared UI uses that identity to select its product experience; there is no runtime variant toggle.
Agent Workspace uses `/dashboard/api/auth/langsmith/login` with the existing loopback PKCE handoff and `/dashboard/api/auth/desktop/exchange` session exchange.
The LangSmith callback is `/dashboard/api/auth/langsmith/callback` on the fixed backend.

## Build locally

From the repository root, with existing workspace dependencies installed:

```sh
pnpm --dir desktop run start:agent-workspace
pnpm --dir desktop run pack:agent-workspace
pnpm --dir desktop run dist:agent-workspace
```

The distribution command targets macOS DMG and zip.
Outputs go to `desktop/dist-agent-workspace/`, separate from `desktop/dist/`.
`desktop/agent-workspace.json` owns Agent Workspace's version independently of Open SWE and the CLI.
The build stamps the variant into the Electron main process; the Agent Workspace packager rejects an Open SWE build.
The ordinary `build`, `start`, `pack`, and `dist` commands still produce Open SWE.
Local signing follows the normal electron-builder credential configuration; the release workflow performs production signing and notarization.

## Releases and updates

Bump `desktop/agent-workspace.json` in a pull request, merge to `main`, then manually run **Release Desktop** on `main` with distribution **agent-workspace**.
This release path is independent of Open SWE releases, production promotion, and Homebrew.
It creates signed and notarized `Agent-Workspace-<version>-<arch>-mac.dmg` and `.zip` assets, plus zip blockmap and `latest-mac.yml`, under `agent-workspace-v<version>`.
There is no Homebrew cask, standalone CLI release, or automatic nightly for Agent Workspace.

The app reads only this generic update feed:

```text
https://github.com/langchain-ai/open-swe/releases/download/agent-workspace-updates/latest-mac.yml
```

The rolling release contains metadata pointing to versioned installer assets, not Open SWE's `latest-mac.yml`.
Both Agent Workspace release tags and its rolling feed are marked as GitHub prereleases and never GitHub's latest release, so Open SWE's existing GitHub updater continues to discover only Open SWE stable releases.
The generic Agent Workspace updater still installs stable semantic versions from its own feed.
Automatic updates use the zip only. The separately signed DMG is notarized and stapled after building, so it is deliberately excluded from updater metadata rather than publishing stale checksums after stapling changes its bytes.
Never publish an Agent Workspace release as the repository's latest release.

### Release prerequisites

The existing `APPLE_SIGNING_CERT`, `APPLE_SIGNING_CERT_PASSWORD`, `APPLE_API_KEY`, `APPLE_API_KEY_ID`, and `APPLE_API_ISSUER` Actions secrets sign and notarize this app too.
The Developer ID must belong to team `H253X88X9F`, as in the existing Open SWE entitlements.
Register `com.langchain.agentworkspace` with Apple before shipping.

For Universal Links, additionally provide `AGENT_WORKSPACE_APPLE_PROVISIONING_PROFILE`, a base64-encoded Developer ID profile for `com.langchain.agentworkspace` with Associated Domains enabled.
The backend's Apple app-site-association document must include `H253X88X9F.com.langchain.agentworkspace` for any paths intended to open in this app.
Without this optional profile, the workflow signs without Associated Domains and logs a warning; login still uses its browser loopback handoff.
When both apps claim a Universal Link, macOS decides which registered app receives it.

The fixed backend must deploy the LangSmith authentication routes and publish a desktop UI archive containing Agent Workspace support before installers are distributed.
Signing, notarization, Apple registration, deployed login, and real update installation require macOS/production credentials and cannot be established by a Linux build alone.
