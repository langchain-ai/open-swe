---
name: maintain-e2e-tests
description: How to keep Playwright E2E tests passing after a UI change. Covers the selector contract, the real-vs-faked boundary, updating test helpers, and running/debugging the suite.
---

# Maintaining E2E tests after a UI change

The E2E suite lives in `tests/e2e/` and uses **Playwright** (Chromium only). Tests
drive the **real built `ui/` React app** — only the LLM and the external SaaS HTTP
boundaries (GitHub API, Slack API) are faked. Everything else (agent code, webhooks,
sandbox, git, dashboard routes, auth) runs for real.

## 1. Understand the selector contract

Tests locate UI elements using a strict preference order:

1. **`data-testid` attributes** — the primary contract between UI and E2E.
   `page.getByTestId("plan-review")`, `page.getByTestId("queued-message")`, etc.
2. **Accessible roles** — `page.getByRole("link", { name: "..." })`,
   `page.getByRole("button", { name: "..." })`,
   `page.getByRole("menuitem", { name: "..." })`.
3. **Placeholder text** — `page.getByPlaceholder(/Add a follow up|.../)`.
4. **CSS / attribute selectors** — used only for mock UIs (`#reset`, `.msg.bot`,
   `a[href*="/pull/"]`, `[data-pr="1"]`) and structural queries
   (`a[href$="/agents/${threadId}"]`).

When changing a UI component, **search for its `data-testid` values and role/name
strings in `tests/e2e/tests/*.spec.ts`** before renaming, removing, or restructuring.

### Current `data-testid` inventory (in `ui/src/`)

| Test ID | Component file | Used in spec |
|---|---|---|
| `review-plan-link` | `AgentThreadView.tsx` | `plan_review.spec.ts` |
| `queued-messages`, `queued-message` | `messages/Messages.tsx` | `dashboard.spec.ts` |
| `workflow-approval-card` | `WorkflowApprovalCard.tsx` | — |
| `plan-review`, `plan-document`, `plan-status`, `plan-decision` | `PlanReview.tsx` | `plan_review.spec.ts` |
| `plan-comment`, `plan-comments`, `comment-input`, `comment-submit`, `comment-delete` | `PlanReview.tsx` | `plan_review.spec.ts` |
| `approve-plan`, `reject-plan`, `edit-plan`, `save-plan`, `cancel-edit-plan`, `copy-plan` | `PlanReview.tsx` | `plan_review.spec.ts` |
| `plan-editor` | `PlanReview.tsx` | — |
| `fake-github-login` | (harness static HTML) | `plan_review.spec.ts` |

## 2. When you change a UI component

### Renaming or removing a `data-testid`

1. Grep for the old test ID across `tests/e2e/tests/`:
   ```
   grep -r 'old-test-id' tests/e2e/tests/
   ```
2. Update every `.spec.ts` reference to match the new ID.
3. If the element is removed entirely, remove or rewrite the assertions that depended
   on it. Don't leave dead `getByTestId` calls.

### Changing placeholder text, button labels, or link names

Tests use `getByPlaceholder(/.../)`  and `getByRole("button", { name: "..." })`.
If you change user-visible text that tests match on, update the corresponding regex
or string in the spec files.

### Restructuring DOM hierarchy

Some tests rely on parent/child traversal:
- `row.locator("..").getByRole("button", { name: "Thread actions" })` —
  the kebab menu sits beside a link's parent.
- `.msg.bot a[href*="/pull/"]` — anchor inside a bot message.
- `.filter({ hasText: "..." })` — content-based filtering on a parent locator.

If you restructure the DOM, verify these structural selectors still resolve.

### Adding a new testable element

When adding UI that E2E tests should cover:
1. Add a `data-testid` attribute in the component.
2. Write assertions in the appropriate `.spec.ts` file (or a new one).
3. Prefer `getByTestId` for elements that have no stable accessible role/name.
4. Prefer `getByRole` when the element has a meaningful ARIA role and label.

## 3. The fake LLM script

The scripted LLM in `tests/e2e/fake_llm.py` emits a **fixed sequence of tool calls**.
If your UI change requires a different agent behavior (e.g. a new tool call, a different
message shape), you must update the scripted model's response sequence. The scripted
model is the **only** faked agent piece.

## 4. Test helpers and control endpoints

Shared helpers are defined at the top of each spec file (not in a separate utils
module). Common patterns:

- `loginAs(page, { login, email })` — mints a real session cookie via
  `POST /control/login`.
- `openThreadViaSlackLink(page)` — resets fakes, sends a Slack message, waits for
  the agent to finish, clicks the bot's "Open in Web" link.
- `botMessages(request)` — polls `GET /mock/slack/messages` for bot-only messages.
- `addComment(page, text, shortcut?)` — fills the comment input and submits via
  click or keyboard shortcut.

If your UI change affects any of these flows (e.g. the composer placeholder changes,
the "Open in Web" link selector changes), update the helpers in **every spec file**
that defines them.

## 5. The harness and mock UIs

- `tests/e2e/harness.py` — the HTTP app serving the real `agent.webapp` + fake
  GitHub/Slack APIs + mock UIs + control endpoints. If you add new dashboard API
  routes that tests need, the harness already mounts the real webapp — no changes
  needed unless you need new fake endpoints.
- `tests/e2e/static/slack.html` and `github.html` — mock UIs for external SaaS.
  These use simple `#id` selectors (`#reset`, `#send`, `#text`, `#thread`). Change
  these only if the fake SaaS rendering needs to match new agent behavior.
- `tests/e2e/fakes.py` — in-memory stores for Slack messages and GitHub PRs. Update
  if the agent's interaction with these fakes changes shape.

## 6. The real dashboard UI build

`tests/e2e/global-setup.ts` builds the `ui/` SPA once before tests run, with
`VITE_DASHBOARD_API_BASE_URL` pointed at the harness. After a UI change:
- The build runs automatically (no action needed unless you change the Vite config
  or port).
- Set `E2E_FORCE_UI_BUILD=1` to force a rebuild if the cached build is stale.
- The build requires Corepack with `pnpm` enabled.

## 7. Running and debugging

```bash
cd tests/e2e
npm install
npx playwright install chromium

# Run all E2E tests (boots langgraph dev automatically)
npx playwright test

# Watch in slow motion
SLOW_MO=700 npx playwright test --headed

# Run a single spec file
npx playwright test tests/dashboard.spec.ts

# View the HTML report after a run
npx playwright show-report

# Replay a specific trace
npx playwright show-trace test-results/<test>/trace.zip
```

Every test records a **trace** (DOM snapshots + network + console) and a **video**.
Failures also capture a screenshot. In CI, these are uploaded as the
`playwright-report` artifact.

## 8. Checklist for a UI change

1. Make the UI change in `ui/src/`.
2. `grep -r 'data-testid\|getByTestId\|getByRole\|getByPlaceholder' tests/e2e/tests/`
   — cross-reference with what you changed.
3. Update selectors, assertions, and helpers in affected spec files.
4. If the change requires different agent behavior, update `fake_llm.py`.
5. Run `npx playwright test` locally.
6. If a test fails, check the trace (`npx playwright show-trace ...`) before guessing
   at fixes — the DOM snapshot shows exactly what was rendered.
7. Commit the UI change and the test updates together.
