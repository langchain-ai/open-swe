# Open SWE Desktop

> [!IMPORTANT]
> This desktop wrapper is experimental. The web UI is the recommended way to use Open SWE.

This package wraps the existing Open SWE dashboard in Electron. It does not fork or bundle a
second renderer, so the web and desktop experiences stay in sync.

## How it connects

The desktop app only selects which dashboard URL to load; it does not select a LangGraph server.
The dashboard's build and deployment configuration determine which dashboard API and LangGraph
deployment receive requests. Signing in creates a session for that configured backend but does
not change the backend selection.

Packaged builds ask for the organization's dashboard URL on first launch and store it in the
app's local user data. They do not default to the Open SWE maintainers' deployment. Local
development loads `http://localhost:3000`, where the UI's `VITE_DASHBOARD_API_BASE_URL`
configuration determines the dashboard API.

## Local development

Run the backend and dashboard as described in `docs/INSTALLATION.md`, then start Electron:

```bash
cd desktop
pnpm install
pnpm run dev
```

Development loads `http://localhost:3000`. Use `--url` to open another dashboard:

```bash
pnpm run start -- --url=https://your-dashboard.example.com
```

`OPEN_SWE_DESKTOP_URL` provides the same override when launching from a shell. Resolution order
is command-line argument, environment variable, saved first-launch configuration, then the local
development default. Use **Open SWE → Dashboard URL…** to change the saved URL.

## Packaging

```bash
pnpm run pack # unpacked application for the current platform
pnpm run dist # installer for the current platform
```

On first launch, packaged builds ask for the organization's dashboard URL. Build outputs are
written to `desktop/dist/`.
