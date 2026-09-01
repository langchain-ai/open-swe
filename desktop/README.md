# Open SWE Desktop

> [!IMPORTANT]
> This desktop client is experimental. The web UI is the recommended way to use Open SWE.

The Electron package ships the compiled Open SWE web UI. Users configure only the URL of a
compatible Open SWE backend; they do not need a separately hosted dashboard.

Desktop users can choose **This Mac** in the new-task composer to run the same Open SWE LangGraph agent over a selected local project. Electron owns a loopback-only LangGraph server, proxies it to the bundled UI, and stops it with the app. Local threads use the same streaming protocol, graph, tools, subagents, and middleware assembly as cloud threads; only the filesystem backend and unavailable cloud integrations differ.

The packaged app bundles its Python runtime and locked Open SWE dependencies. Source development uses `uv run langgraph dev`. Provider credentials stay in the local LangGraph process and are not inherited by agent shell commands. Added projects and local thread history are persisted in the desktop app's local data.

For local OpenAI models, the app can use either `OPENAI_API_KEY` or Sign in with ChatGPT. When no
API key is configured, sending the first local task opens the system browser for sign-in. OAuth
credentials are encrypted with the operating system's secure storage, refreshed by Electron, and
made available to the local model client through an authenticated loopback broker. Refresh tokens
are never placed in the local backend environment or inherited by agent shell commands.

Local model calls also honor `LANGSMITH_GATEWAY_*` configuration. On managed macOS installs, the
app reads `LC_GATEWAY_KEY` from `launchctl` when no explicit gateway key is configured and enables
gateway routing for the local backend. Gateway and provider credentials are not inherited by agent
shell commands.

The side panel's **Changes** tab diffs the project against a git snapshot taken when the session
started, so it shows what the agent changed and not the working tree's prior state. It also shows
the current branch and discovers its pull request when the GitHub CLI is installed and authenticated.

## How it connects

The bundled UI runs at an internal `open-swe://app` origin. Electron proxies its
`/dashboard/api/*` requests to the selected backend, so the browser never receives a LangSmith API
key and never calls the raw LangGraph API directly. GitHub login creates the same signed dashboard
session used by the web UI.

Packaged builds ask for the organization's backend URL on first launch and store it in the app's
local user data. They have no maintainer-hosted default. Use **Open SWE → Backend URL…** to switch
deployments; switching clears the previous deployment's local session data.

The backend's GitHub App must allow `<backend-url>/dashboard/api/auth/callback` as a callback URL.
Set `ALLOWED_GITHUB_ORGS` on the backend to prevent GitHub users outside the organization from
creating dashboard sessions.

The desktop sign-in screen also offers **Continue in local mode**. This skips GitHub sign-in and
limits the Agents workspace to projects and threads on **This Mac**; cloud threads, settings, and
other account-backed features remain behind sign-in. The choice is remembered on that computer,
and **Sign in for cloud mode** remains available from the local sidebar.

## Install on macOS

Install Git, Node.js 22, and `uv`, clone this repository, then run this from its root:

```bash
make install-desktop
```

The command fast-forwards to the latest `main`, builds Open SWE Desktop, and installs it in
`/Applications` (or `~/Applications` when needed). Run it again to update and replace the app; saved
backend settings, login sessions, and projects are preserved.

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

`pnpm run dev:desktop` is equivalent to `make desktop`. The desktop app starts its private local-agent backend on a random loopback port while connecting cloud features and GitHub login to the shared backend at `http://localhost:2024`.

Source launches use an isolated `Open SWE Development` Electron profile, so the dev app can run
beside an installed `Open SWE` app without sharing its login session, backend configuration,
projects, or single-instance lock. The dev window is labeled **Open SWE Development**; its first
launch may require signing in and adding projects again.

A separate agent installation is not required. Confirm `uv --version` succeeds before starting the desktop app in development.

Development defaults to `http://localhost:2024`. Point to another backend with:

```bash
pnpm --dir desktop run start -- --backend-url=https://open-swe-api.example.com
```

`OPEN_SWE_BACKEND_URL` provides the same override. Resolution order is command-line argument,
environment variable, saved first-launch configuration, then the local development default.
The original `--url` and `OPEN_SWE_DESKTOP_URL` names remain accepted for compatibility.

## Packaging

```bash
pnpm --dir desktop run pack # unpacked application for the current platform
pnpm --dir desktop run dist # installer for the current platform
```

Both commands build `ui/` and package its static output with Electron. Build outputs are written
to `desktop/dist/`.

## macOS releases

`desktop/package.json` is the latest stable version. Every **Promote main to prod** run publishes a
prerelease nightly from the promoted commit with a UTC timestamp, such as
`desktop-v0.2.3-nightly.20260902080000`. Nightly releases never publish the stable version.

To publish a stable release, bump `desktop/package.json` in a normal pull request. Merging that
change to `main` automatically runs **Release Desktop** for the exact package version. The workflow
fails if that stable release is already complete and remains manually runnable to retry a partial
release.

Both paths build the current `ui/` bundle, sign and notarize the Electron app, verify the resulting
app and DMG, create the tag, and publish the DMG, macOS zip, and app zip to a GitHub release.
Desktop-prefixed tags keep this release stream separate from web and backend releases; the workflow
packages the web UI but does not deploy or otherwise change the hosted web app.

The workflow requires these GitHub Actions secrets:

- `APPLE_SIGNING_CERT`: base64-encoded Developer ID Application `.p12` certificate
- `APPLE_SIGNING_CERT_PASSWORD`: password for the certificate
- `APPLE_API_KEY`: App Store Connect `.p8` key contents
- `APPLE_API_KEY_ID`: App Store Connect key ID
- `APPLE_API_ISSUER`: App Store Connect issuer ID

Local packaging remains available without those credentials; signing and notarization are performed
by the release workflow.

## Deployment security

The backend URL is public configuration, not a credential. Dashboard routes require an
`osw_session` cookie issued after GitHub login, and `ALLOWED_GITHUB_ORGS` controls who may complete
that login. CORS alone is not access control.

Raw LangGraph routes are a separate boundary. A deployment using `LANGGRAPH_AUTH_TYPE=noop` must
keep those routes behind a private network, authenticated gateway, or custom LangGraph auth. An
external user does not need the deployment's server-side `LANGSMITH_API_KEY` to call an exposed,
unauthenticated LangGraph route.
