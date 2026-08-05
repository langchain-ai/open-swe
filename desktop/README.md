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

The packaged app loads `https://openswe.vercel.app`, so it uses the same API and LangGraph
deployment as that web UI. Local development loads `http://localhost:3000`, where the UI's
`VITE_DASHBOARD_API_BASE_URL` configuration determines the dashboard API.

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

`OPEN_SWE_DESKTOP_URL` provides the same override when launching from a shell. Command-line
configuration takes precedence over the environment variable.

## Packaging

```bash
pnpm run pack # unpacked application for the current platform
pnpm run dist # installer for the current platform
```

Packaged builds open `https://openswe.vercel.app` by default. Build outputs are written to
`desktop/dist/`.
