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

## The App token, and why `gh` covers it

Code paths that want an App installation token rather than the caller's — a published
review and its diff, inline review comments, the reviewer trigger — fall back to the
`gh` CLI's token under `langgraph dev` (`agent/github/app.py::_local_dev_token`). So
they work locally, and `503 GitHub App token unavailable` means `gh` is logged out,
not that the feature needs an App.

The fallback is gated on the runtime being `langgraph dev`, so it never widens a
deployed installation's reach. **That gate is why a standalone script cannot trigger
agent work**: `uv run python -c "...trigger_re_review(...)"` fails with `No GitHub App
token available`, because `dev_login_enabled()` is false outside the server. Drive
those paths over HTTP instead (below).

## Running a real agent

Agent and reviewer runs work locally against real models and real sandboxes. Nothing
is stubbed; runs cost money and take minutes.

1. `.env` needs a model key — `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. With both set,
   `DEFAULT_MODEL_ID` resolves to the OpenAI model; with only Anthropic, to Claude.

2. Set `SANDBOX_TYPE=local`. `langsmith` sandboxes **cannot work from a laptop**: the
   run PATCHes a proxy config whose `match_hosts` is the hostname of your
   `DASHBOARD_API_BASE_URL`, and the sandbox API rejects `localhost` with a bare
   `422 unknown` whose body never reaches the log. It fails in
   `PrepareReviewerRunMiddleware.before_agent`, so the agent never starts and the
   review settles with zero findings — which reads exactly like a model that chose to
   say nothing. Either use `local`, or expose the dashboard on a real hostname
   (`make tunnel`, see docs/DEVELOPMENT.md) before using `langsmith`.

3. Get a session cookie, then post the trigger. Mutations are CSRF-checked against
   `DASHBOARD_BASE_URL`, so send a matching `Origin` — without it you get
   `403 CSRF check failed`:

   ```bash
   curl -sS -c /tmp/osw.txt -o /dev/null 'http://127.0.0.1:2026/dashboard/api/auth/dev-login?redirect_to=/review'
   curl -sS -b /tmp/osw.txt -X POST -H 'Origin: http://localhost:3000' \
     http://127.0.0.1:2026/dashboard/api/reviews/OWNER/REPO/NUMBER/re-review
   ```

   A `{"success": true, "thread_id": "..."}` means the run is queued, not finished.

4. Watch it in the backend log. The run is minutes long and the failures worth
   catching are sandbox provisioning and model auth, so grep for both rather than only
   for the tool you care about:

   ```bash
   tail -f logs/backend.log | grep -E --line-buffered "sandbox|Traceback|error_detail=[^N]|publish_review"
   ```

5. Reviewer output lands where the dashboard reads it — findings in reviewer thread
   metadata, guidance points in `pull_request_guidance`. Check the table directly when
   a page looks empty, to tell "the agent recorded nothing" apart from "the read path
   is broken":

   ```bash
   docker exec open-swe-postgres psql -U postgres -d postgres \
     -c "SELECT kind, author, summary FROM open_swe.pull_request_guidance"
   ```

A local checkout has never run an agent, so no thread carries the messages that code
reading a PR's human turns looks for. `scripts/seed_local_author_guidance.py` writes them
into one thread's checkpoint and links it to a real PR.

Two traps when writing thread state by hand. `update_state` refuses a thread with no
`graph_id`, normally set by its first run — set it yourself with `threads.update`. And
`messages` appends, so an empty list clears nothing and the server will not coerce a
`RemoveMessage` arriving as state; re-seeding means deleting the thread and recreating it.

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
