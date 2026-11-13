import { test, expect } from "@playwright/test";

test.describe("Slack run usage footer", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("shows neither the model nor the cost on the final reply", async ({
    page,
  }) => {
    await page
      .locator("#text")
      .fill("<@U0BOT> please add a greet() helper and open a PR");
    await page.locator("#send").click();

    const reply = page
      .locator(".msg.bot")
      .filter({ hasText: "Add greet() helper" });
    await expect(reply).toBeVisible();
    await expect(
      reply.getByRole("link", { name: "Open in Robin" }),
    ).toHaveCount(1);
    await expect(reply).not.toContainText("fake-scripted-model");
    await expect(reply).not.toContainText("calculating cost");
    await expect(reply).not.toContainText("$");
  });
});
