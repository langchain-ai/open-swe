# Open SWE Desktop

> [!IMPORTANT]
> This desktop client is experimental. The web UI is the recommended way to use Open SWE.

The Electron package ships the compiled Open SWE web UI. Users configure only the URL of a
compatible Open SWE backend; they do not need a separately hosted dashboard.

Desktop users can choose **This Mac** in the new-task composer to run an agent over a selected local project. The thread is an ordinary cloud thread on the connected backend — same transcript, models, settings, MCPs and skills — whose sandbox is the project on this Mac instead of a hosted box. The app serves it through a sandbox bridge: it long-polls the backend for the agent's `execute`, `upload_files` and `download_files` requests, runs them in the checkout, and posts the results back. This is the same bridge `oswe run` uses, from the shared `bridge-client` package.

Commands run with your login shell's environment, minus anything secret-shaped except `GITHUB_TOKEN` and `GH_TOKEN`, so `git` and `gh` use your own identity and credentials. A thread runs only while the app that started it is open: quitting closes its bridge, and a run started meanwhile reports the Mac as unreachable. Opening the thread again, or sending a follow-up, reopens it. The thread is visible from the web and other computers, but only this Mac can continue it.

Threads started before this change ran on a private loopback LangGraph server the app bundles. They are still listed under This Mac and run on that server, with their own `OPENAI_API_KEY` or ChatGPT sign-in; new threads always use the bridge. The local server will be removed once those threads have aged out.

The composer's workspace selector chooses where a local thread runs. **Current checkout** (the default) runs the agent in the project directory itself, on whichever branch the branch picker selects. **New worktree** gives the thread its own git worktree, checked out from the selected base branch on a placeholder `open-swe/local-<id>` branch that the agent renames after it reads the request — so your own checkout is never touched and several local threads can run at once without contending for a working tree. Deleting a thread removes its worktree along with anything uncommitted in it. Only one agent may work in a given tree at a time, so starting a thread in a checkout another agent is running in, or switching that checkout's branch under it, is refused.

The side panel's **Changes** tab diffs the project against a git snapshot taken when the session
started, so it shows what the agent changed and not the working tree's prior state. It also shows
the workspace's branch and discovers its pull request when the GitHub CLI is installed and authenticated.
The **Terminal** and file browser open in the same checkout, on this Mac.

## How it connects

The bundled UI runs at an internal `open-swe://app` origin. Electron proxies its
`/dashboard/api/*` requests to the selected backend, so the browser never receives a LangSmith API
key and never calls the raw LangGraph API directly. GitHub login creates the same signed dashboard
session used by the web UI.

Packaged builds ask for the organization's backend URL on first launch and store it in the app's
local user data. They have no maintainer-hosted default. Use **Open SWE → Backend URL…** to switch
deployments; switching clears the previous deployment's local session data.

The shared backend's GitHub App must allow `<backend-url>/dashboard/api/auth/callback` as a
callback URL. Set `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS` on that backend to control which
GitHub users can create dashboard sessions, including the ones "This Mac" threads run under.

## Install on macOS

Install Git, Node.js 22, `uv`, and [Bun](https://bun.com/docs/installation), clone this
repository, then run this from its root:

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

`pnpm run dev:desktop` is equivalent to `make desktop`. The desktop app connects to the backend at `http://localhost:2024`, which runs "This Mac" threads too; the bridge needs that backend's PostgreSQL.

Source launches use an isolated `Open SWE Development` Electron profile, so the dev app can run
beside an installed `Open SWE` app without sharing its login session, backend configuration,
projects, or single-instance lock. The dev window is labeled **Open SWE Development**; its first
launch may require signing in and adding projects again.

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

Stable releases use a deliberate bump, test, release process: bump `desktop/package.json` in a normal
pull request, merge it to `main`, test the resulting code as needed, then run **Release Desktop**
manually. The workflow publishes the exact package version and fails if that stable release is
already complete. A partial stable release remains manually retryable.

Both paths compile the current `ui/` bundle, sign and notarize the Electron app, verify the resulting
app and DMG, create the tag, and publish the DMG, macOS zip, and app zip. Desktop-prefixed tags keep
this release stream separate from web and backend releases; the workflow packages the web UI but
does not deploy or otherwise change the hosted web app.

The workflow requires these GitHub Actions secrets:

- `APPLE_SIGNING_CERT`: base64-encoded Developer ID Application `.p12` certificate
- `APPLE_SIGNING_CERT_PASSWORD`: password for the certificate
- `APPLE_PROVISIONING_PROFILE`: base64-encoded Developer ID provisioning profile for
  `com.langchain.openswe` with Associated Domains enabled (optional; enables Universal Links)
- `APPLE_API_KEY`: App Store Connect `.p8` key contents
- `APPLE_API_KEY_ID`: App Store Connect key ID
- `APPLE_API_ISSUER`: App Store Connect issuer ID

Local packaging remains available without those credentials; signing and notarization are performed
by the release workflow.

## Deployment security

The backend URL is public configuration, not a credential. Dashboard routes require an
`osw_session` cookie issued after GitHub login, and `ALLOWED_GITHUB_ORGS` or
`ALLOWED_GITHUB_USERS` controls who may complete that login. CORS alone is not access control.

Raw LangGraph routes are a separate boundary. A deployment using `LANGGRAPH_AUTH_TYPE=noop` must
keep those routes behind a private network, authenticated gateway, or custom LangGraph auth. An
external user does not need the deployment's server-side `LANGSMITH_API_KEY` to call an exposed,
unauthenticated LangGraph route.
