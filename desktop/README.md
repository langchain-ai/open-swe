# Open SWE Desktop

> [!IMPORTANT]
> This desktop client is experimental. The web UI is the recommended way to use Open SWE.

The Electron package ships the compiled Open SWE web UI and a local TypeScript graph server. A
hosted Open SWE backend is optional and is needed only for cloud features.

Desktop users can choose **This Mac** in the new-task composer to run the generic coding agent over
an allowlisted local project. Electron owns loopback-only graph and TanStack servers, connects them
to the bundled UI, and stops them with the app. Local threads use the standard graph streaming
protocol and local filesystem and shell tools. Cloud integrations are unavailable in local mode.

The packaged app bundles Node.js 24 and its locked TypeScript dependencies. Desktop development and
packaging use pnpm and do not invoke or bundle Python. Provider credentials stay in the local graph
process and are not inherited by agent shell commands. Added projects and local thread history are
persisted in the desktop app's local data.

For local OpenAI models, the app can use either `OPENAI_API_KEY` or Sign in with ChatGPT. When no
API key is configured, sending the first local task opens the system browser for sign-in. OAuth
credentials are kept in Electron memory, refreshed by Electron, and made available to the local
model client through an authenticated loopback broker. The desktop app does not invoke operating-
system credential storage. Refresh tokens are never placed in the local backend environment or
inherited by agent shell commands. A valid shared local OpenAI login cache can also be used
read-only.

The side panel's **Changes** tab diffs the project against a git snapshot taken when the session
started, so it shows what the agent changed and not the working tree's prior state. It also shows
the current branch and discovers its pull request when the GitHub CLI is installed and authenticated.

## How it connects

The bundled TanStack server runs on a private loopback origin. It proxies local graph requests with
a process-private bearer token, so the renderer never receives provider credentials or direct
access to the graph server. When a hosted backend is configured, its dashboard requests use the
same signed session as the web UI.

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

Install Git, Node.js 24.18.1 (the version in `.node-version`), and pnpm, clone this repository,
then run this from its root:

```bash
make install-desktop
```

The command fast-forwards to the latest `main`, builds Open SWE Desktop, and installs it in
`/Applications` (or `~/Applications` when needed). Run it again to update and replace the app; saved
backend settings, login sessions, and projects are preserved.

## Local development

Install the workspace dependencies, then start the TypeScript graph, TanStack, and Electron
processes together:

```bash
pnpm install                  # from the repo root
make desktop
```

`pnpm run desktop:dev:full` is the equivalent workspace command. Both commands stop the backend when
the desktop app exits, and stop the desktop app if the backend fails.

Source launches use an isolated `Open SWE Development` Electron profile, so the dev app can run
beside an installed `Open SWE` app without sharing its login session, backend configuration,
projects, or single-instance lock. The dev window is labeled **Open SWE Development**; its first
launch may require signing in and adding projects again.

A separate agent installation and Python are not required.

Development starts in local-only mode. Enable cloud features by pointing it to a hosted backend:

```bash
pnpm --dir desktop run start -- --backend-url=https://open-swe-api.example.com
```

`OPEN_SWE_BACKEND_URL` provides the same override. Resolution order is command-line argument,
environment variable, then saved configuration.
The original `--url` and `OPEN_SWE_DESKTOP_URL` names remain accepted for compatibility.

## Packaging

```bash
pnpm --dir desktop run pack # unpacked application for the current platform
pnpm --dir desktop run dist # installer for the current platform
```

Both commands build the TanStack application and TypeScript graph, bundle the exact Node.js 24
runtime, and package them with Electron. Build outputs are written to `desktop/dist/`; packaged
resources are rejected if they contain Python source, a Python runtime, or `uv`.

## macOS releases

Maintainers can run **Release Desktop** from the GitHub Actions page on `main` and choose a patch,
minor, or major version bump. The workflow builds the current `ui/` bundle, signs and notarizes the
Electron app, verifies the resulting app and DMG, bumps `desktop/package.json`, creates a
`desktop-vX.Y.Z` tag, and publishes the DMG, macOS zip, and app zip to a GitHub release. The
desktop-prefixed tags keep this release stream separate from web and backend releases; the workflow
packages the web UI but does not deploy or otherwise change the hosted web app.

The workflow requires these GitHub Actions secrets:

- `RELEASE_PAT`: token allowed to push to `main` and create tags
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
