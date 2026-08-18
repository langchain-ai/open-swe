import { test, expect, type Page } from "@playwright/test";

// Drives the REAL built ui/ app against a run that fans out to two subagents
// via deepagents' `task` tool. Only the LLM boundary is scripted (see
// fake_llm.py's "subagents" script); the nested loops, the `task` tool, and the
// SDK's subagent discovery are all real.
//
// One test, not one per assertion: each fan-out is a full agent run against a
// single shared sandbox, so separate tests queue behind each other and the
// harness falls over. This walks one run from delegation to settled instead.
const USER = { login: "alice", email: "alice@example.com" };

const SCOUT_TASK = "Scout the repository layout";
const AUDITOR_TASK = "Audit the repository for missing tests";

// The scripted subagents sleep (15s and 25s) so the page is open while they
// run: nested activity arrives over SSE only and never replays from a
// checkpoint.
test.describe.configure({ timeout: 180_000 });

async function loginAs(page: Page, user: { login: string; email: string }) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
}

/**
 * Ask in mock Slack for a fan-out, then follow the bot's "Open in Web" link to
 * the live dashboard thread. The bot acknowledges before delegating, so the
 * link appears while the subagents are still running.
 */
async function openFanOutThread(page: Page) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page
    .locator("#text")
    .fill("<@U0BOT> please fan out to subagents and report what they find");
  await page.locator("#send").click();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible({ timeout: 60000 });
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

/**
 * A settled turn folds its work log — including the subagent cards — behind a
 * single "Worked for …" row. Open it so post-run state stays assertable.
 */
async function expandWorkFold(page: Page) {
  const fold = page.getByRole("button", { name: /^Worked( for .+)?$/ }).first();
  if ((await fold.getAttribute("aria-expanded")) !== "true") {
    await fold.click();
  }
}

test.beforeEach(async ({ page }) => {
  await loginAs(page, USER);
});

test("renders subagents from delegation through to their results", async ({
  page,
}) => {
  await openFanOutThread(page);

  // --- Delegated: one card per subagent, both in flight -------------------
  const cards = page.getByTestId("subagent-card");
  await expect(cards).toHaveCount(2, { timeout: 120000 });

  const scout = cards.filter({ hasText: SCOUT_TASK });
  const auditor = cards.filter({ hasText: AUDITOR_TASK });
  await expect(scout).toHaveCount(1);
  await expect(auditor).toHaveCount(1);
  await expect(scout).toHaveAttribute("data-subagent", "general-purpose");

  // Both are delegated in one model turn, so both run concurrently.
  await expect(
    page.locator('[data-testid="subagent-card"][data-status="running"]'),
  ).toHaveCount(2);

  // The header counts the fan-out and reads as present tense while it runs.
  // Which member has finished depends on where in the run the page landed, so
  // assert the shape of the counter rather than an instantaneous value.
  await expect(page.getByTestId("subagent-group-headline")).toHaveText(
    /Running \d\/2 subagents/,
  );

  // --- Live: nested activity and a ticking timer --------------------------
  const activity = scout.getByTestId("subagent-activity");
  await expect(activity).toBeVisible({ timeout: 60000 });

  const elapsed = auditor.getByTestId("subagent-elapsed");
  await expect(elapsed).toBeVisible();
  const firstElapsed = await elapsed.innerText();
  await expect(elapsed).not.toHaveText(firstElapsed, { timeout: 15000 });

  // --- Expanding a card reveals the steps a collapsed one hides -----------
  // The scout runs two `execute` calls; collapsed, the card shows only the
  // latest.
  await expect(activity).toHaveAttribute("data-step-count", "2", {
    timeout: 60000,
  });
  await expect(scout.getByTestId("subagent-activity-step")).toHaveCount(1);
  await scout.getByRole("button").first().click();
  await expect(scout.getByTestId("subagent-activity-step")).toHaveCount(2);

  // --- Settled: results surface, timers freeze ----------------------------
  await expect(
    page.getByRole("button", { name: /^Worked( for .+)?$/ }).first(),
  ).toBeVisible({ timeout: 90000 });
  await expandWorkFold(page);

  await expect(
    page.locator('[data-testid="subagent-card"][data-status="completed"]'),
  ).toHaveCount(2);
  await expect(page.getByTestId("subagent-group-headline")).toHaveText(
    "Ran 2 subagents",
  );

  await expect(scout.getByTestId("subagent-result")).toContainText("Scout done");
  await expect(auditor.getByTestId("subagent-result")).toContainText(
    "Audit done",
  );

  const settledElapsed = await elapsed.innerText();
  await page.waitForTimeout(3000);
  expect(await elapsed.innerText()).toBe(settledElapsed);
});
