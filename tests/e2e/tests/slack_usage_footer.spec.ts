import { test, expect } from "@playwright/test";

test.describe("Slack run usage footer", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("updates the final reply with model, tokens, and cost", async ({ page, request }) => {
    await page.locator("#text").fill("<@U0BOT> please add a greet() helper and open a PR");
    await page.locator("#send").click();

    const reply = page.locator(".msg.bot").filter({ hasText: "Add greet() helper" });
    await expect(reply).toBeVisible();
    await expect(reply.getByRole("link", { name: "Open in Web" })).toHaveCount(1);

    const completion = await request.post("/control/run-complete");
    expect(completion.ok()).toBe(true);
    expect(await completion.json()).toEqual({ status: "ok", reason: "usage footer updated" });

    await expect(reply).toContainText("fake-scripted-model");
    await expect(reply).toContainText("12.3K tokens");
    await expect(reply).toContainText("$0.42");
    await expect(reply.getByRole("link", { name: "Open in Web" })).toHaveCount(1);
  });
});
