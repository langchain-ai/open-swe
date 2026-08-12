import { test, expect } from "@playwright/test";

type SlackStatus = {
  status?: string;
  loading_messages?: string[];
};

test.describe("Slack assistant loading tips", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("includes a tip on every active status update", async ({ page }) => {
    await page.locator("#text").fill("<@U0BOT> please add a greet() helper and open a PR");
    await page.locator("#send").click();

    await expect(page.locator(".msg.bot").filter({ hasText: "Add greet() helper" })).toBeVisible();

    const response = await page.request.get("/mock/slack/statuses");
    const statuses = (await response.json()) as SlackStatus[];
    const activeStatuses = statuses.filter(({ status }) => status);

    expect(activeStatuses.length).toBeGreaterThan(0);
    for (const status of activeStatuses) {
      expect(status.loading_messages).toHaveLength(1);
      expect(status.loading_messages?.[0]).toMatch(/^Tip: /);
    }
  });
});
