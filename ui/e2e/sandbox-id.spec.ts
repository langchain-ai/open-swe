import { test, expect, type Page } from "@playwright/test"

const SANDBOX_ID = "sb-e2e-abc123"

const USER = {
  login: "octocat",
  email: "octocat@example.com",
  avatar_url: null,
  is_admin: false,
}

const THREAD = {
  id: "11111111-1111-1111-1111-111111111111",
  title: "E2E sandbox thread",
  repo: "open-swe",
  repoFullName: "langchain-ai/open-swe",
  branch: "main",
  model: "Default",
  status: "finished",
  viewed: true,
  isOwner: true,
  createdAt: 1_700_000_000_000,
  updatedAt: 1_700_000_000_000,
  traceUrl: "https://smith.langchain.com/o/trace/xyz",
  sandboxId: SANDBOX_ID,
  messages: [],
}

const SIDEBAR = {
  active: { items: [THREAD], limit: 50, hasMore: false },
  resolved: { items: [], limit: 20, hasMore: false },
}

const OPTIONS = {
  models: [],
  default_agent_model: "",
  default_agent_reasoning_effort: "",
  default_agent_subagent_model: "",
  default_agent_subagent_reasoning_effort: "",
}

async function mockDashboardApi(page: Page): Promise<void> {
  // Catch-all first; Playwright checks the most-recently-registered route
  // first, so the specific handlers below win. Everything else 200s empty so
  // unrelated queries don't retry/error and slow the page down.
  await page.route("**/dashboard/api/**", (route) => route.fulfill({ json: {} }))
  await page.route("**/dashboard/api/me", (route) =>
    route.fulfill({ json: USER })
  )
  await page.route("**/dashboard/api/threads/sidebar*", (route) =>
    route.fulfill({ json: SIDEBAR })
  )
  await page.route("**/dashboard/api/options", (route) =>
    route.fulfill({ json: OPTIONS })
  )
  await page.route("**/dashboard/api/repos", (route) =>
    route.fulfill({ json: { installations: [], repositories: [] } })
  )
  // A default model on the profile keeps the first-run onboarding modal from
  // opening and trapping focus over the sidebar under test.
  await page.route("**/dashboard/api/profile", (route) =>
    route.fulfill({ json: { default_model: "Default", reasoning_effort: "medium" } })
  )
}

const threadRow = (page: Page) =>
  page.getByRole("link", { name: /E2E sandbox thread/ })

const copyItem = (page: Page) =>
  page.getByRole("menuitem", { name: "Copy sandbox ID" })

test.beforeEach(async ({ page }) => {
  await mockDashboardApi(page)
  await page.goto("/agents")
  await expect(threadRow(page)).toBeVisible()
})

test("desktop: right-click menu copies the sandbox id", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop pointer only")

  // The touch-only kebab must stay hidden when the device can hover.
  await expect(page.getByRole("button", { name: "Thread actions" })).toBeHidden()

  await threadRow(page).click({ button: "right" })
  await expect(copyItem(page)).toBeVisible()
  await copyItem(page).click()

  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip).toBe(SANDBOX_ID)
})

test("iPad: kebab menu copies the sandbox id", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "ipad-touch", "touch pointer only")

  // No right-click / hover on iPad — the kebab is the entry point and must be
  // visible without hovering.
  const kebab = page.getByRole("button", { name: "Thread actions" })
  await expect(kebab).toBeVisible()
  await kebab.tap()

  await expect(copyItem(page)).toBeVisible()
  await copyItem(page).tap()

  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip).toBe(SANDBOX_ID)

  // Tapping the kebab must open the menu, not follow the row's Link into the
  // thread.
  await expect(page).toHaveURL(/\/agents$/)
})
