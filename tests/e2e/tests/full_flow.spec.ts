import { test, expect } from "@playwright/test";

// Full happy path, driven entirely through the mock Slack + GitHub UIs:
//   user asks in Slack -> real agent implements in a local sandbox -> opens a PR
//   on the fake GitHub -> replies with the PR link in the SAME Slack thread.
// Only the LLM is faked; all agent code runs for real via langgraph dev.
test.describe("Open SWE full flow", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("Slack request → implements → opens PR → links it back in the thread", async ({ page }) => {
    await page.locator("#text").fill("<@U0BOT> please add a greet() helper and open a PR");
    await page.locator("#send").click();

    // The user's message lands in the thread.
    await expect(page.locator(".msg").filter({ hasText: "add a greet() helper" })).toBeVisible();

    // The agent replies in the SAME thread with a link to the PR it opened.
    const reply = page.locator(".msg.bot").filter({ hasText: "Add greet() helper" });
    await expect(reply).toBeVisible();
    const prLink = reply.locator('a[href*="/pull/"]');
    await expect(prLink).toBeVisible();

    // Follow the link → the PR page shows what GitHub would show.
    await prLink.click();
    await expect(page.locator("#pr-title")).toContainText("Add greet() helper");
    await expect(page.locator("#pr-state")).toHaveText("open");
    await expect(page.locator("#pr-head")).toHaveText("add-greet");
    await expect(page.locator('#pr-files li[data-file="greet.py"]')).toBeVisible();

    // The PR list view shows it too.
    await page.goto("/mock/github");
    await expect(page.locator('.pr[data-pr="1"]')).toContainText("Add greet() helper");
    await expect(page.locator('.pr[data-pr="1"]')).toContainText("greet.py");
  });

  test("Slack breakout request starts a new top-level Open SWE thread", async ({ page }) => {
    await page.locator("#text").fill("<@U0BOT> please break out adding a greet() helper into a separate thread");
    await page.locator("#send").click();

    const breakout = page
      .locator(".msg.bot")
      .filter({ hasText: /Open SWE breakout thread:\* Add greet\(\) helper/ });
    await expect(breakout).toBeVisible({ timeout: 60_000 });
    const breakoutThreadTs = await breakout.getAttribute("data-thread-ts");
    expect(breakoutThreadTs).toBeTruthy();

    const breakoutThreadMessages = page.locator(`.msg.bot[data-thread-ts="${breakoutThreadTs}"]`);
    await expect(breakoutThreadMessages.locator('a[href*="/agents/"]')).toBeVisible({
      timeout: 60_000,
    });
    await expect(
      page.locator(".msg.bot").filter({ hasText: "I started a separate Open SWE thread" }),
    ).toBeVisible({ timeout: 60_000 });
  });

  test("a message that does not mention the bot produces no run and no PR", async ({ page }) => {
    await page.locator("#mention").uncheck();
    await page.locator("#text").fill("just chatting with the team, nothing for the bot");
    await page.locator("#send").click();

    await expect(page.locator(".msg").filter({ hasText: "just chatting" })).toBeVisible();
    // No agent activity: give the (non-)run a moment, then assert nothing came back.
    await page.waitForTimeout(3000);
    await expect(page.locator(".msg.bot")).toHaveCount(0);

    const prs = await (await page.request.get("/mock/github/data")).json();
    expect(prs.length).toBe(0);
  });
});
