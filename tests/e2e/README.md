# Playwright E2E — the full Slack → implement → PR → reply flow

This drives the **whole happy path** through two mock UIs:

1. A user asks Open SWE to implement something in a **mock Slack** thread.
2. The **real agent** runs (via `langgraph dev`): it implements the change in a
   **local temp-dir sandbox**, pushes a branch, and opens a PR on a **fake GitHub**.
3. It posts the PR link back to the **same Slack thread** — visible in the mock UI.

## What is faked vs. real

Only the **LLM** and the **external SaaS HTTP boundaries** are faked. All agent
code runs for real.

| Piece | Real or fake |
|---|---|
| Slack webhook → `process_slack_mention` → run dispatch | **real** (`agent.webapp`) |
| `get_agent`, deepagents loop, tools, middleware, prompt | **real** |
| `open_pull_request`, `slack_thread_reply` tools | **real** |
| Sandbox | **real** `local` provider, rooted in a throwaway temp dir |
| Git remote ("GitHub") | **real git**, a local bare repo the agent clones/pushes |
| The LLM | **fake** — a scripted model (`fake_llm.py`) emitting a fixed tool sequence |
| `api.github.com` REST (PR create) | **fake** (`/fake-gh/...`), state rendered at `/mock/github` |
| `slack.com/api` (post message, etc.) | **fake** (`/fake-slack/...`), thread rendered at `/mock/slack` |
| GitHub App token mint, `api.github.com/user` identity | stubbed (offline) |

The fake GitHub/Slack stores are the single source of truth the mock UIs render,
so what Playwright asserts on is exactly what the real agent produced.

## Files

- `e2e_env.py` — env + constants set before any `agent.*` import (sandbox=local,
  fake API URLs, isolated `GIT_CONFIG_GLOBAL`, bot-token-only mode).
- `fake_llm.py` — the scripted `BaseChatModel` (the only faked agent piece).
- `patches.py` — monkeypatches the boundaries (LLM, GitHub/Slack URLs, token mint).
- `agent_entrypoint.py` — langgraph `agent` graph: applies patches, re-exports the
  real `traced_agent`.
- `harness.py` — langgraph `http.app`: the real `agent.webapp` plus the fake
  GitHub/Slack APIs, the mock UIs, and the control/compose endpoints.
- `fakes.py` — in-memory PR/Slack stores + git seeding of the bare remote.
- `langgraph.e2e.json` — dev-server config pointing at the two entrypoints above.
- `static/{slack,github}.html` — the mock Slack/GitHub UIs (external SaaS we can't
  run locally). The dashboard is **not** mocked — it's the real `ui/` app.
- `global-setup.ts` — builds the real `ui/` SPA (once) so the harness can serve it.

## The dashboard — the real `ui/` app

The dashboard is **not** mocked. The bot's "Open in Web" link
(`DASHBOARD_BASE_URL/agents/{thread_id}`) loads the **actual built `ui/` React
app** — served same-origin from the harness so the session cookie and
`/dashboard/api/*` calls work without CORS. The signed session cookie is real
(minted via `/control/login`), so per-user authorization is genuine; the only
extra fake is the OAuth-token store (an external credential).

The UI is built by `global-setup.ts` with `VITE_DASHBOARD_API_BASE_URL` pointed at
the harness. It builds once; set `E2E_FORCE_UI_BUILD=1` to rebuild (e.g. after a
UI change or port change). Requires `bun`.

## Run

```bash
cd tests/e2e
npm install
npx playwright install chromium
npx playwright test          # boots langgraph dev automatically, then runs
```

Watch it in human time:

```bash
SLOW_MO=700 npx playwright test --headed
```

## Artifacts (replay a run)

Every test records a **trace** (DOM-snapshot timeline + network + console + source)
and a **video**; failures also get a screenshot. Locally they land in
`test-results/<test>/` and are embedded in `playwright-report/`:

```bash
npx playwright show-report                       # browse runs; each has a Trace tab
npx playwright show-trace test-results/<test>/trace.zip   # open one trace directly
```

In CI the `Playwright E2E` job uploads both `playwright-report/` and
`test-results/` as the **playwright-report** artifact on the run. Download it,
then `npx playwright show-report <unzipped-dir>` (or drag a `trace.zip` onto
<https://trace.playwright.dev>) to replay.

Poke at it by hand (from the repo root):

```bash
uv run langgraph dev --config tests/e2e/langgraph.e2e.json --port 2024 \
  --no-browser --allow-blocking --no-reload
# open http://127.0.0.1:2024/mock/slack  and  /mock/github
```
