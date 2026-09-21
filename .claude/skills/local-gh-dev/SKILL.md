---
name: local-gh-dev
description: Run the Open SWE dashboard locally against real GitHub data, signed in with the `gh` CLI instead of a per-machine GitHub App. Use when asked to run the app locally, test a dashboard change against real PRs, reproduce something a screenshot shows, or when local login fails with "GITHUB_APP_CLIENT_ID not configured".
---

# Local dashboard on real GitHub data

The dashboard's per-user reads — the PR list, one PR's details, a PR preview — run on
the signed-in person's own OAuth token. `gh` already holds one, so local development
needs no GitHub App: `/dashboard/api/auth/dev-login` turns `gh`'s credentials into a
session. It is refused unless `langgraph dev` is the runtime, and the
`ALLOWED_GITHUB_USERS` gate still applies.

## Start it

1. Confirm `gh` is logged in. `gh auth status` — if not, `gh auth login`.

2. Check the ports. The backend wants 2024 and Vite wants 3000:

   ```bash
   lsof -nP -iTCP:3000,2024 -sTCP:LISTEN
   ```

   Other worktrees run their own backends and E2E harnesses on 2024. Never kill one
   without asking. Use another port instead and point Vite at it — every command below
   takes `PORT` for exactly that reason.

3. Make sure `.env` has these. The GitHub App keys are *not* needed; these are:

   | Key | Why |
   |---|---|
   | `DASHBOARD_JWT_SECRET` | signs the session; any random string |
   | `ALLOWED_GITHUB_USERS` | your `gh api user --jq .login` |
   | `POSTGRES_URI` | skips the container step when one is already up |
   | `DASHBOARD_BASE_URL` | `http://localhost:3000` |
   | `DASHBOARD_API_BASE_URL` | `http://localhost:3000` |

   Startup aborts with `ALLOWED_GITHUB_ORGS or ALLOWED_GITHUB_USERS must be configured`
   when the allowlist is missing.

4. Start the backend. `make dev` covers this when 2024 is free and you want its
   postgres container; otherwise run it directly:

   ```bash
   uv run langgraph dev --no-browser --port 2026 --n-jobs-per-worker 10
   ```

5. Start Vite against it:

   ```bash
   DASHBOARD_API_URL=http://127.0.0.1:2026 pnpm run dev
   ```

6. Open `http://localhost:3000` and press **Continue with GitHub**. With no App
   configured it redirects to the `gh` login and lands you signed in. To skip the page,
   go straight to `/dashboard/api/auth/dev-login?redirect_to=/agents/reviews`.

## Postgres

`make dev` starts an `open-swe-postgres` container unless `POSTGRES_URI` is set, and
fails with a name conflict when another worktree already started one. Reuse it:

```bash
docker ps --filter name=open-swe-postgres --format '{{.Names}} {{.Status}} {{.Ports}}'
```

It binds `127.0.0.1:5433`, so `POSTGRES_URI=postgresql://postgres:postgres@127.0.0.1:5433/postgres`.

## What does not work without a GitHub App

Anything reading through an App installation token rather than the caller's:

- a published review and its diff (`/reviews/{owner}/{repo}/{number}` and `/diff`)
- inline review comment reads and writes
- webhook-driven flows

These answer `503 GitHub App token unavailable`. That is expected locally, not a
regression. The reviews **list** and each PR's **preview** do work, because they read
as you.

## Checking a page

Use the `ego-browser` skill — it drives a real browser and keeps the session cookie.
`curl` gets a 401 or the login redirect, because it has no session.

Measure rather than eyeball when the question is about layout or density:

```js
await page.evaluate(() => {
  const r = (e) => e && e.getBoundingClientRect()
  return { viewport: [innerWidth, innerHeight], first: r(document.querySelector('li')) }
})
```

## Rebuilding after a change

Vite hot-reloads `ui/src`. Python changes need the backend restarted — `langgraph dev`
watches files, but a failed import leaves it down, so check it came back:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:2026/dashboard/api/me
```

`401` is healthy: the server is up and asking for a session. `000` means it is down —
read the backend log.
