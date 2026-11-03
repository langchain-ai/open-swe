---
name: dashboard-preview
description: Drive this checkout's dashboard (`ui/`) against the shared preview deployment's real threads, with no local backend. Use to verify a UI change in a browser, count the requests a page makes, or compare a page's behaviour before and after a change.
---

# Dashboard against preview

`pnpm run dev:prod` serves this checkout's `ui/` from Vite and proxies the API to a deployment with a real session, so every page shows real threads.
Point it at preview, never production:

```bash
DASHBOARD_API_URL=https://open-swe-preview-cc53e8fbe667565d843d0843f84ee92c.us.langgraph.app pnpm run dev:prod
```

The first run opens the user's browser for a GitHub handoff.
When they are signed in to GitHub it completes on its own, and the session is cached in `~/.cache/open-swe/dev-session.json` for a week.
If `pnpm` fails with `No version is set for shim: pnpm`, run it as `mise exec pnpm@<version> -- pnpm run dev:prod`, taking the version from `packageManager` in `package.json`.

## Browser pane

The in-app Browser pane needs a launch configuration that starts the server; one that only names a URL is refused.
Write `.claude/launch.json` (leave it uncommitted), then `preview_start` `dashboard-preview`:

```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "dashboard-preview",
      "runtimeExecutable": "pnpm",
      "runtimeArgs": ["run", "dev:prod"],
      "env": { "DASHBOARD_API_URL": "https://open-swe-preview-cc53e8fbe667565d843d0843f84ee92c.us.langgraph.app" },
      "port": 3000
    }
  ]
}
```

Vite holds port 3000 strictly: stop any `dev:prod` you started from Bash first.
The `ego-browser` skill works too, and it carries the user's real login for both preview and production.

## The session is real

Reading is free.
A message sent from the composer starts a real run, and opening a pull request's review page creates its chat thread.
Ask the user before either.

## Finding a thread to test on

Only threads with `transcript: "v2"` read the transcript log; older ones answer `/transcript` with 404 and go through the SDK stream.
Rank candidates by size from the page:

```js
const { items } = await (await fetch("/dashboard/api/threads/page?limit=40")).json()
for (const t of items.filter((t) => t.transcript === "v2")) {
  const s = await (await fetch(`/dashboard/api/threads/${t.id}/transcript`)).json()
  console.log(t.id, t.status, s.tool_calls.length, t.title)
}
```

## Counting requests

Wrap `fetch`, then move with the router so the wrapper survives; a reload drops it.
The dashboard's API calls all go through `window.fetch` at call time:

```js
window.__origFetch ??= window.fetch
window.__hits = []
window.fetch = (input, init) => {
  window.__hits.push(typeof input === "string" ? input : input.url)
  return window.__origFetch(input, init)
}
await window.__TSR_ROUTER__.navigate({ to: "/agents/$threadId", params: { threadId } })
await new Promise((r) => setTimeout(r, 15000))
window.__hits.filter((u) => u.includes("/tool-calls/")).length
```

Each `preview_eval` times out after 30 seconds, so keep the wait under that.

## Before and after

Measure the branch, then put the base branch's version of each changed file in place, reload, and measure again:

```bash
git diff main --name-only -- ui/src | grep -v test | while read f; do git show "main:$f" > "$f"; done
```

Vite picks the files up; navigate to `/agents` with a full reload before measuring.
Restore with `git checkout HEAD -- ui/src` and check `git status` is clean before you commit.
