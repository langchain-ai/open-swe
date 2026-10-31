# Agent Workspace Desktop

> [!IMPORTANT]
> This desktop client is experimental.
> The web UI is the recommended way to use Open SWE.

The Electron app downloads a standalone UI release selected by the connected backend.
It keeps the last complete UI on disk, including lazy routes, for offline use.
No dashboard is embedded in the installer.
First launch requires connectivity to download the UI; failure shows a retry screen.

Desktop users can choose **This Mac** in the new-task composer to run an agent over a selected local project.
The thread is an ordinary cloud thread on the connected backend — same transcript, models, settings, MCPs and skills — whose sandbox is the project on this Mac instead of a hosted sandbox.
The app serves it through a sandbox bridge: it long-polls the backend for the agent's `execute`, `upload_files` and `download_files` requests, runs them in the checkout, and posts the results back.
This is the same bridge `oswe run` uses, from the shared `bridge-client` package.

Commands run with your login shell's environment, minus anything secret-shaped except `GITHUB_TOKEN` and `GH_TOKEN`, so `git` and `gh` use your own identity and credentials.
A thread runs only while the app that started it is open: quitting closes its bridge, and a run started meanwhile reports the Mac as unreachable.
Opening the thread again, or sending a follow-up, reopens it.
The thread is visible from the web and other computers, but only this Mac can continue it.

In **LangSmith Agent Workspace**, new chats need no repository or folder selection.
This Mac creates a private task folder per conversation under the app profile’s `tasks/<thread-id>` directory.
**Use a folder** optionally selects existing material; several chats can select the same folder, without sharing their conversation histories.
**Open task folder** opens the working location in Finder.
Closing, archiving, or deleting a chat does not delete its automatic task files.
These folders are starting locations, not filesystem security sandboxes.
Git branch and worktree controls appear only for Git folders.

The general-purpose experience is saved on new threads before their first run and inherited by later runs and task workers.
Existing conversations retain their coding experience.
Testing requires this branch’s desktop shell and backend; a local UI against an older backend cannot enable the new prompting.

The composer's workspace selector chooses where a local thread runs.
**Current checkout** (the default) runs the agent in the project directory itself, on whichever branch the branch picker selects.
**New worktree** gives the thread its own git worktree, checked out from the selected base branch on a placeholder `open-swe/local-<id>` branch that the agent renames after it reads the request — so your own checkout is never touched and several local threads can run at once without contending for a working tree.
Deleting a thread removes its worktree along with anything uncommitted in it.
Only one agent may work in a given tree at a time, so starting a thread in a checkout another agent is running in, or switching that checkout's branch under it, is refused.

The side panel's **Changes** tab diffs the project against a git snapshot taken when the session started, so it shows what the agent changed and not the working tree's prior state.
It also shows the workspace's branch and discovers its pull request when the GitHub CLI is installed and authenticated.
The **Terminal** and file browser open in the same checkout, on this Mac.

## How it connects

The UI runs at `open-swe://app`, with native IPC for local projects, threads, worktrees, terminals and files.
`/desktop-ui.json` on the backend selects a GitHub Release archive by backend commit, SHA-256 and bridge protocol version.
Downloads carry no session credentials, follow redirects only to GitHub release hosts, and are verified before activation.
Each backend has its own cache.
Cached UI opens immediately; updates download in the background and take effect on the next reload.
Interrupted or corrupt downloads leave the previous UI intact.
Running agents requires the backend.

**Release Desktop UI** publishes `ui.json.gz` and `descriptor.json` as immutable `desktop-ui-<commit>` prereleases on main/preview pushes.
Backend images select that exact commit through their stamped build identity.
To run a local UI instead, build `ui/` (`pnpm --filter open-swe-dashboard run build`) and launch the app with `OPEN_SWE_DESKTOP_UI_ARCHIVE=<repo>/ui/.output/desktop/ui.json.gz`; it installs that archive on every load instead of contacting the backend's descriptor.

Electron proxies `/dashboard/api/*` requests to the selected backend, so the browser never receives a LangSmith API key and never calls the raw LangGraph API directly.
Choose LangSmith or GitHub sign-in from the desktop login screen.
Native navigation opens the selected provider in your browser with a fresh PKCE loopback handoff; both providers create a signed dashboard session.

Packaged builds ask for the organization's backend URL on first launch and store it in the app's local user data.
They have no maintainer-hosted default.
Use **Agent Workspace → Backend URL…** to switch deployments; switching clears the previous deployment's local session data.

When the backend the app is connected to sets `OPENSWE_ALTERNATIVE_ENVS="preview:https://...,staging:https://..."`, **Agent Workspace → Environment** lists those backends next to it.
Choosing one switches only until the app restarts; the stored backend URL stays the same.
Nothing is configured on the desktop side.

On macOS, Universal Links open agent and review pages in the app only when the URL's origin matches the configured backend.
Links for other deployments open in the default browser instead.
Associated domains are fixed in the signed app, so macOS may still launch Agent Workspace before it hands a mismatched link back to the browser.

The shared backend's GitHub App must allow `<backend-url>/dashboard/api/auth/callback` as a callback URL.
Set `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS` on that backend to control which GitHub users can create dashboard sessions, including the ones "This Mac" threads run under.

## Install on macOS

On Apple Silicon Macs running macOS Ventura or later, you can install with Homebrew:

```sh
brew install --cask langchain-ai/tap/open-swe-desktop
```

This installs the desktop app and links `oswe` onto your `PATH`.
The CLI updates with the app.
If you installed the cask before it linked `oswe`, run `brew reinstall --cask open-swe-desktop` once to get the link (a plain `brew upgrade` skips auto-updating casks).

To build and install from source, install Git, Node.js 22, and [Bun](https://bun.com/docs/installation), clone this repository, then run this from its root:

```bash
make install-desktop
```

The command fast-forwards to the latest `main`, builds Agent Workspace, and installs it in `/Applications` (or `~/Applications` when needed).
Run it again to update and replace the app; saved backend settings, login sessions, and projects are preserved.

## Local development

Install the workspace dependencies, then run the backend, desktop app, and web UI independently:

```bash
pnpm install # from the repo root

# terminal 1
make dev

# terminal 2
make desktop

# terminal 3 (optional web UI)
make web
```

`pnpm run dev:desktop` is equivalent to `make desktop`.
The desktop app connects to the backend at `http://localhost:2024`, which runs "This Mac" threads too; the bridge needs that backend's PostgreSQL.

The app keeps the original `com.langchain.openswe` bundle ID and `Open SWE` production Electron profile, preserving installed users’ sessions, projects, task folders, caches, and instance locks.
Packaged apps continue to share backend configuration and sessions with `oswe` through `~/.open-swe/config.json`.
Source launches retain the isolated `Open SWE Development` profile and do not share CLI configuration.
The dev window is labeled **Agent Workspace Development**; existing development sessions and projects are preserved.

Development defaults to `http://localhost:2024`.
Point to another backend with:

```bash
pnpm --dir desktop run start -- --backend-url=https://open-swe-api.example.com
```

`OPEN_SWE_BACKEND_URL` provides the same override.
Resolution order is command-line argument, environment variable, saved first-launch configuration, then the local development default.
The original `--url` and `OPEN_SWE_DESKTOP_URL` names remain accepted for compatibility.

## Packaging

```bash
pnpm --dir desktop run pack # unpacked application for the current platform
pnpm --dir desktop run dist # installer for the current platform
```

Neither command builds or embeds the dashboard.
Build outputs are written to `desktop/dist/`; the bundle is `Agent Workspace.app`.
Release assets retain the `Open-SWE-*` names, `desktop-v*` tags, original stable update feed and `open-swe-desktop` Homebrew cask for compatibility.
There is one desktop product and version, not separate coding and general-purpose distributions.
Before publishing the renamed stable app, update the `langchain-ai/homebrew-tap` cask to install `Agent Workspace.app` and link its `Contents/Resources/bin/oswe`; the existing cask still expects `Open SWE.app`.
An update from an installed Open SWE app and a Homebrew reinstall still need macOS smoke tests to verify bundle replacement, preserved sessions/projects, and the CLI link.

## macOS releases

`desktop/package.json` is the latest stable version.
Every **Promote main to prod** run publishes a prerelease nightly from the promoted commit with a UTC timestamp, such as `desktop-v0.2.3-nightly.20260902080000`.
Nightly releases never publish the stable version.

Stable releases use a deliberate bump, test, release process: bump `desktop/package.json` in a normal pull request, merge it to `main`, test the resulting code as needed, then run **Release Desktop** manually.
The workflow publishes the exact package version and fails if that stable release is already complete.
A partial stable release remains manually retryable.

Both paths sign and notarize the Electron shell, verify the app and DMG, create the tag, and publish the installers.
UI releases are separate from desktop shell releases.

The workflow requires these GitHub Actions secrets:

- `APPLE_SIGNING_CERT`: base64-encoded Developer ID Application `.p12` certificate
- `APPLE_SIGNING_CERT_PASSWORD`: password for the certificate
- `APPLE_PROVISIONING_PROFILE`: base64-encoded Developer ID provisioning profile for `com.langchain.openswe` with Associated Domains enabled (optional; enables Universal Links)
- `APPLE_API_KEY`: App Store Connect `.p8` key contents
- `APPLE_API_KEY_ID`: App Store Connect key ID
- `APPLE_API_ISSUER`: App Store Connect issuer ID

Local packaging remains available without those credentials; signing and notarization are performed by the release workflow.

## Deployment security

The backend URL is public configuration, not a credential.
Dashboard routes require an `osw_session` cookie issued after GitHub or LangSmith login.
`ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS` controls GitHub login; `LANGSMITH_LOGIN_ORG_ID` controls LangSmith organization authorization.
CORS alone is not access control.

Raw LangGraph routes are a separate boundary.
A deployment using `LANGGRAPH_AUTH_TYPE=noop` must keep those routes behind a private network, authenticated gateway, or custom LangGraph auth.
An external user does not need the deployment's server-side `LANGSMITH_API_KEY` to call an exposed, unauthenticated LangGraph route.

## LangSmith deployment

Register a confidential OAuth client (dynamic registration only permits MCP access) with callback `<DASHBOARD_API_BASE_URL>/dashboard/api/auth/langsmith/callback`.
Configure `LANGSMITH_CONNECTION_URL` (normally `https://api.smith.langchain.com`), `LANGSMITH_OAUTH_CLIENT_ID`, and `LANGSMITH_OAUTH_CLIENT_SECRET` on the backend, alongside its existing dashboard URLs, JWT/encryption secrets and PostgreSQL configuration.
Set `LANGSMITH_LOGIN_ORG_ID=f5c798a2-2155-4999-ad27-6d466bd26e1c` for the production Agent Workspace organization.
LangSmith login is disabled when this setting is unset, even if personal LangSmith connections are configured.
Apply migrations through `0089_1850da6129ee`.
Never ship the client secret in the app.

Login discovers OIDC endpoints on the issuer origin and requests `openid profile email offline_access` for the API resource.
Users must select the organization configured by `LANGSMITH_LOGIN_ORG_ID`.
Membership requires both `/userinfo`'s acting-org claim and current `/orgs?skip_create=true` membership; the claim alone does not prove membership.
The organization list is unpaginated.

The desktop starts `/dashboard/api/auth/langsmith/login` with a PKCE challenge and loopback port.
The backend returns a short-lived handoff code to `127.0.0.1`; `/auth/desktop/exchange` requires the verifier.
No browser session is created, and the handoff cannot itself authenticate as a cookie.
LangSmith identities have independent internal UUID accounts, without GitHub identities or email-based linking.

Encrypted `langsmith_login` grants refresh automatically and also serve managed tools.
Every authenticated request, run dispatch, runtime call and worker grant checks the stored grant.
Successful organization authorization is cached in PostgreSQL for at most 60 seconds, bound to the current grant and configured organization; provider revocation or membership removal can take up to 60 seconds to take effect.
Expired authorization must be revalidated; temporary provider failures deny access without extending the cache or discarding the grant.
Only tokens nearing expiry acquire the cross-replica refresh lock, and refreshed identities are checked before storing their grants.
Sign out deletes the stored grant, immediately denying subsequent sessions/runtime calls, and attempts remote refresh/access-token revocation at the issuer's advertised RFC 7009 endpoint.
Remote revocation failures do not prevent local sign out; a new login is required to restore backend access.
`/me` renews valid seven-day sessions on loading/focus; sessions inactive past expiry need login again.
Sign out rather than disconnecting the login grant.

Tasks and workers use private local bridges, with automatic task folders or an optional existing folder.
Cloud moves, GitHub operations, and borrowed deployment bot/App credentials are unavailable.
Deploy the login routes and desktop UI with both sign-in providers before distributing installers.
Real confidential-client OAuth, macOS signing/install and update installation still need production smoke tests; Linux packaging and mocked integration tests do not establish deployment readiness.
