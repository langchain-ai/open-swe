# Open SWE Desktop

> [!IMPORTANT]
> This desktop client is experimental. The web UI is the recommended way to use Open SWE.

The Electron package ships the compiled Open SWE web UI. Users configure only the URL of a
compatible Open SWE backend; they do not need a separately hosted dashboard.

Desktop users can choose **This Mac** in the new-task composer to run the Python
`deepagents-code` agent over ACP in a selected local project. The web dashboard does not expose
this option. The desktop app launches the user's installed `dcode --acp`, inheriting its
authentication and configuration. It finds the standard `~/.local/bin/dcode` installation even
when a packaged app does not inherit the terminal's `PATH`; `OPEN_SWE_DCODE_COMMAND` overrides the
executable path. Added projects are persisted in the desktop app's local data and can be selected
from the **This Mac** submenu or managed from the sidebar. Local dcode runs are ephemeral: their
sessions remain available only for the lifetime of the desktop process and cannot be resumed after
it exits.

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

## Deployment security

The backend URL is public configuration, not a credential. Dashboard routes require an
`osw_session` cookie issued after GitHub login, and `ALLOWED_GITHUB_ORGS` controls who may complete
that login. CORS alone is not access control.

Raw LangGraph routes are a separate boundary. A deployment using `LANGGRAPH_AUTH_TYPE=noop` must
keep those routes behind a private network, authenticated gateway, or custom LangGraph auth. An
external user does not need the deployment's server-side `LANGSMITH_API_KEY` to call an exposed,
unauthenticated LangGraph route.
