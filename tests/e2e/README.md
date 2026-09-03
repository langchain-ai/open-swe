# Playwright E2E — the full Slack → implement → PR → reply flow

This drives the **whole happy path** through two mock UIs:

1. A user asks Open SWE to implement something in a **mock Slack** thread.
2. The **real agent** runs (via `langgraph dev`): it implements the change in a
   **local temp-dir sandbox**, pushes a branch, and opens a PR on a **fake GitHub**.
3. It posts the PR link back to the **same Slack thread** — visible in the mock UI.

The **real reviewer graph** runs here too, so the two review paths can be
asserted against each other: a PR Open SWE opened is reviewed inline by the
authoring thread (findings on the thread, nothing on the PR), and anyone else's
PR gets the webhook reviewer's inline comments. `self_review.spec.ts` and
`auto_review_standdown.spec.ts` cover the pair.

## What is faked vs. real

Only the **LLM** and the **external SaaS HTTP boundaries** are faked. All agent
code runs for real.

| Piece                                                            | Real or fake                                                               |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Slack webhook → `process_slack_mention` → run dispatch           | **real** (`agent.webapp`)                                                  |
| `get_agent`, deepagents loop, tools, middleware, prompt          | **real**                                                                   |
| `open_pull_request`, `slack_thread_reply` tools                  | **real**                                                                   |
| Sandbox                                                          | **real** `local` provider, rooted in a throwaway temp dir                  |
| Git remote ("GitHub")                                            | **real git**, a local bare repo the agent clones/pushes                    |
| The LLM                                                          | **fake** — a scripted model (`fake_llm.py`) emitting a fixed tool sequence |
| `api.github.com` REST + GraphQL — PR create, raw diff, reviews, inline comments and replies, issue comments, check runs, thread resolve — plus dashboard GitHub OAuth login | **fake** (`/fake-gh/...`), state rendered at `/mock/github` |
| Reviewer graph, findings storage, `publish_review`, the review check run | **real** |
| `slack.com/api` (post message, etc.)                             | **fake** (`/fake-slack/...`), thread rendered at `/mock/slack`             |
| Environment tools, store records, snapshot naming + status       | **real**                                                                   |
| Electron UI, main process, IPC, git diff                         | **real**                                                                   |
| Pinned uv `dcode --acp`, tools, and local project                | **real**; only its model class points at `fake_llm.py`                      |
| LangSmith snapshot service (capture/delete)                      | **fake** (`patches.py`) — the local sandbox has nothing to snapshot         |
| GitHub App token mint, `api.github.com/user` identity            | stubbed (offline)                                                          |

The fake GitHub/Slack stores are the single source of truth the mock UIs render,
so what Playwright asserts on is exactly what the real agent produced.

## Files

- `e2e_env.py` — env + constants set before any `agent.*` import (sandbox=local,
  fake API URLs, isolated `GIT_CONFIG_GLOBAL`, bot-token-only mode).
- `fake_llm.py` — the scripted `BaseChatModel` (the only faked agent piece). A
  script is chosen per turn from markers in the request text; the reviewer graph
  instead gets a model pinned to the `reviewer` script, since its turn arrives as
  webhook input with no prompt text to route on.
- `patches.py` — monkeypatches the boundaries (LLM, GitHub/Slack URLs, token mint).
- `agent_entrypoint.py` — langgraph `agent` graph: applies patches, re-exports the
  real `traced_agent`.
- `harness.py` — langgraph `http.app`: the real `agent.webapp` plus the fake
  GitHub/Slack APIs, the mock UIs, and the control/compose endpoints.
- `reviewer_apis.py` — the fake GitHub *review* surfaces (raw PR diff, reviews,
  inline comments + replies, issue comments, check runs, repo contents) and the
  control endpoints that drive them: `/control/review-state`,
  `/control/open-pull-request` (a PR Open SWE did not open, so it carries no
  inline-review claim), `/control/github-webhook` (a signed delivery to the real
  `/webhooks/github`), `/control/review-repo-enabled`, and
  `/control/forget-review-state` (drops a PR's reviewer thread + inline-review
  claim; PR numbers restart at 1 on reset, and that state outlives the process).
- `reviewer_entrypoint.py` — langgraph `reviewer` graph: patches, then the real
  `traced_reviewer_agent`.
- `fakes.py` — in-memory PR/Slack/review stores (reviews, inline comments, issue
  comments, check runs) + git seeding of the bare remote. PR base/head shas are
  real commits, so the reviewer can check out and diff what it reviews.
- `langgraph.e2e.json` — dev-server config pointing at the entrypoints above.
- `static/{slack,github}.html` — the mock Slack/GitHub UIs (external SaaS we can't
  run locally). The dashboard is **not** mocked — it's the real `ui/` app.
- `global-setup.ts` — builds the real `ui/` SPA (once) so the harness can serve it.
- `playwright.desktop.config.ts` + `tests/desktop.spec.ts` — launch Electron and drive
  the real pinned dcode ACP flow against the same fake model and GitHub state.

## The dashboard — the real `ui/` app

The dashboard is **not** mocked. The bot's "Open in Web" link
(`DASHBOARD_BASE_URL/agents/{thread_id}`) loads the **actual built `ui/` React
app** — served same-origin from the harness so the session cookie and
`/dashboard/api/*` calls work without CORS. The signed session cookie is real
(minted via `/control/login`), so per-user authorization is genuine; the only
extra fake is the OAuth-token store (an external credential).

The UI is built by `global-setup.ts` with both API bases pointed at the harness,
which then runs the app's own Nitro server on `E2E_UI_PORT` (default 3100). The
harness proxies page requests to it, so the specs exercise real server rendering
— the root session gate, the redirect, hydration — instead of a static shell.
`ssr.spec.ts` asserts that on the raw response, because a server-rendering
regression falls back to the client and otherwise passes unnoticed.

It builds once; set `E2E_FORCE_UI_BUILD=1` to rebuild (e.g. after a UI change or
port change). Requires `pnpm`.

## Run

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm run test:e2e            # browser suite
pnpm run test:e2e:desktop    # Electron + pinned uv dcode ACP
```

Watch it in human time:

```bash
SLOW_MO=700 pnpm exec playwright test --headed
```

The webServer is reused locally, so re-running a single spec against a warm
`langgraph dev` is the fast iteration loop — prefer that over the whole suite:

```bash
pnpm exec playwright test tests/full_flow.spec.ts
```

## Artifacts (replay a run)

Recording costs real time on every spec, so browser tests keep a **trace**
(DOM-snapshot timeline + network + console + source) and a **video** only for a
failed attempt; failures also get a screenshot. Set `E2E_ARTIFACTS=1` to capture
both unconditionally, which is what you want when a spec passes but does the
wrong thing. The Desktop test records an Electron trace and a success
screenshot. Artifacts land in `test-results/<test>/` and `playwright-report/`:

```bash
pnpm exec playwright show-report                       # browse runs; each has a Trace tab
pnpm exec playwright show-trace test-results/<test>/trace.zip   # open one trace directly
```

In CI the browser shards upload **playwright-report-1**, **playwright-report-2**,
and **playwright-report-3**; Desktop uploads **playwright-report-desktop**. Each
contains `playwright-report/` and `test-results/`. Download the relevant artifact,
then `pnpm exec playwright show-report <unzipped-dir>` (or drag a `trace.zip` onto
<https://trace.playwright.dev>) to replay.

Poke at it by hand (from the repo root):

```bash
uv run langgraph dev --config tests/e2e/langgraph.e2e.json --port 2024 \
  --no-browser --allow-blocking --no-reload
# open http://127.0.0.1:2024/mock/slack  and  /mock/github
```
