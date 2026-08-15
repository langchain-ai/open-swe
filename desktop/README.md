# Open SWE Desktop

> [!IMPORTANT]
> This desktop client is experimental. The web UI is the recommended way to use Open SWE.

The Electron package ships the compiled Open SWE web UI. Users configure only the URL of a
compatible Open SWE backend; they do not need a separately hosted dashboard.

Desktop users can choose **This Mac** in the new-task composer to run the Python
`deepagents-code` agent over ACP in a selected local project. The web dashboard does not expose
this option. The desktop app passes the selected model and reasoning effort to the user's installed
`dcode --acp`, inheriting its authentication and configuration. Missing local provider credentials
or packages fail during startup and surface in the composer. The app finds the standard
`~/.local/bin/dcode` installation even when a packaged app does not inherit the terminal's `PATH`;
`OPEN_SWE_DCODE_COMMAND` overrides the executable path. Added projects are persisted in the desktop
app's local data and can be selected from the **This Mac** submenu or managed from the sidebar. Local
dcode runs are ephemeral: their sessions remain available only for the lifetime of the desktop
process and cannot be resumed after it exits.

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

## Install on macOS

Install Git and Node.js 22, clone this repository, then run this from its root:

```bash
make install-desktop
```

The command fast-forwards to the latest `main`, builds Open SWE Desktop, and installs it in
`/Applications` (or `~/Applications` when needed). Run it again to update and replace the app; saved
backend settings, login sessions, and projects are preserved.

## Local development

Install both packages, run the backend at `http://localhost:2024`, then start Electron:

```bash
pnpm install                  # from the repo root
pnpm run dev:desktop
```

Source launches use an isolated `Open SWE Development` Electron profile, so the dev app can run
beside an installed `Open SWE` app without sharing its login session, backend configuration,
projects, or single-instance lock. The dev window is labeled **Open SWE Development**; its first
launch may require signing in and adding projects again.

The Python dcode CLI must also be installed and configured. Confirm it is available with
`dcode --version` before starting the desktop app.

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
