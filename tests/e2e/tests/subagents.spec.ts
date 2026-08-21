import { test, expect, type Page } from "@playwright/test";

const USER = { login: "alice", email: "alice@example.com" };
const BASE_URL = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;
const SAME_ORIGIN_HEADERS = { origin: BASE_URL, referer: `${BASE_URL}/` };

async function login(page: Page) {
  const response = await page.request.post("/control/login", { data: USER });
  expect(response.ok()).toBeTruthy();
}

// The home composer needs a default model on the profile before it sends.
async function saveDefaultModel(page: Page) {
  const optionsResponse = await page.request.get("/dashboard/api/options");
  expect(optionsResponse.ok()).toBeTruthy();
  const options = (await optionsResponse.json()) as {
    default_agent_model: string;
    default_agent_reasoning_effort: string;
  };
  const response = await page.request.put("/dashboard/api/profile", {
    headers: SAME_ORIGIN_HEADERS,
    data: {
      default_model: options.default_agent_model,
      reasoning_effort: options.default_agent_reasoning_effort,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function typeIntoComposer(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

// The fake model answers an `E2E_DELEGATE` request by spawning two `task`
// subagents in one turn; each subagent runs a slow shell step first (see
// fake_llm.py) so the nested activity is observable live. The run is started
// from the dashboard composer on purpose: dashboard-submitted runs use the v2
// event protocol that streams the subagents' nested tool events, which the
// legacy Slack dispatch path does not.
async function startDelegatingThread(page: Page) {
  await page.goto("/agents");
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  await typeIntoComposer(page, "E2E_DELEGATE investigate the repository");
  await expect(page).toHaveURL(/\/agents\/[0-9a-f-]+$/);
}

const FINISHED = "Both subagents finished their investigation.";

test.describe("subagents", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await saveDefaultModel(page);
  });

  test("renders a card per subagent with live nested activity", async ({
    page,
  }) => {
    await startDelegatingThread(page);

    // Work is folded while the run streams; the two `task` calls count as
    // actions, so the fold row is labelled "… · 2 actions".
    const fold = page.getByRole("button", { name: /· \d+ actions?$/ });
    await expect(fold).toHaveAttribute("aria-expanded", "false");
    await fold.click();

    const cards = page.getByTestId("subagent-card");
    await expect(cards).toHaveCount(2);
    await expect(cards.nth(0)).toContainText("general-purpose");
    await expect(cards.nth(0)).toContainText("inspect the repository layout");
    await expect(cards.nth(1)).toContainText("list the top-level files");

    // While the subagents run, each card shows its current nested tool call
    // and a step count, straight from the SDK's scoped tools projection.
    const activity = cards.nth(0).getByRole("button", { name: /step/ });
    await expect(activity).toBeVisible();
    await expect(activity).toContainText("Execute");
    await expect(activity).toHaveAttribute("aria-expanded", "false");
    await activity.click();
    await expect(activity).toHaveAttribute("aria-expanded", "true");
    await expect(cards.nth(0).getByTestId("subagent-step")).toHaveCount(1);

    // Both subagents finish with two shell steps each.
    await expect(cards.nth(0).getByTestId("subagent-step")).toHaveCount(2);
    await expect(activity).toContainText("2 steps");
    await expect(page.getByText(FINISHED)).toBeVisible();
  });

  test("keeps the subagent cards on a cold load of the finished thread", async ({
    page,
  }) => {
    await startDelegatingThread(page);
    await expect(page.getByText(FINISHED)).toBeVisible({ timeout: 60_000 });

    await page.reload();
    await page
      .getByRole("button", { name: /^Worked(?: for .+)? · \d+ actions?$/ })
      .click();
    const cards = page.getByTestId("subagent-card");
    await expect(cards).toHaveCount(2);
    await expect(cards.nth(1)).toContainText("list the top-level files");
  });
});
