# Open SWE Desktop

> [!IMPORTANT]
> This desktop client is experimental. The web UI is the recommended way to use Open SWE.

The Electron package ships the compiled Open SWE web UI. Users configure only the URL of a
compatible Open SWE backend; they do not need a separately hosted dashboard.

Desktop users can choose **This Mac** in the new-task composer to run Open SWE directly in a
selected local project. Electron supervises one local LangGraph process bound to a random
`127.0.0.1` port and protects it with a per-launch bearer token. The renderer reaches it only through
the same-origin `/local-graph/*` protocol route, so neither the token nor the raw port enters browser
state. Source launches run the repository graph with `uv`; packaged apps use the bundled Python
runtime and local backend built by `build:local-backend`. No `dcode` installation is required; local
model credentials are read from the environment that launched Open SWE.

Added projects, LangGraph state, and local thread metadata are atomically persisted under Electron
`userData`. Local threads retain their model, effort, pending first prompt, status, and git checkpoint
across restarts;
an interrupted running thread is reconciled to `error`. Project paths are realpath-checked against
the desktop project allowlist before thread creation and again by the local graph. The side panel's
**Changes** tab diffs the project against the retained git checkpoint. Checkpoint refs survive app
quit and are removed only when the user explicitly deletes the local thread.

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

## Local development

Install both packages, run the backend at `http://localhost:2024`, then start Electron:

```bash
pnpm --dir ui install
pnpm --dir desktop install
pnpm --dir desktop run dev
```

Source launches use an isolated `Open SWE Development` Electron profile, so the dev app can run
beside an installed `Open SWE` app without sharing its login session, backend configuration,
projects, or single-instance lock. The dev window is labeled **Open SWE Development**; its first
launch may require signing in and adding projects again.

Install `uv`, sync the repository Python environment, and set the selected model provider's API key
in the environment before starting the desktop app. Electron starts
`uv run langgraph dev --config langgraph.desktop.json` on demand for local threads.

Development defaults to `http://localhost:2024` for cloud/dashboard traffic. Point to another
backend with:

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

Both commands build `ui/`, run `build:local-backend` to assemble a platform-specific managed Python
runtime with the Open SWE package, and package both resources with Electron. Build outputs are
written to `desktop/dist/`. Packaging therefore requires `uv` and network access to download the
managed Python runtime and locked Python dependencies.

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
