import { test, expect } from "@playwright/test";

test.describe("Slack run usage footer", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("omits pending cost on the final reply", async ({ page, request }) => {
    await page
      .locator("#text")
      .fill("<@U0BOT> please add a greet() helper and open a PR");
    await page.locator("#send").click();

    const reply = page
      .locator(".msg.bot")
      .filter({ hasText: "Add greet() helper" });
    await expect(reply).toBeVisible();
    await expect(reply).toContainText("fake-scripted-model");
    await expect(reply).not.toContainText("calculating cost");
    await expect
      .poll(async () => {
        const response = await request.post("/control/slack-run-complete");
        return response.status();
      })
      .toBe(200);
    await expect(reply).not.toContainText("calculating cost");
    await expect(reply).not.toContainText("$");
    await expect(
      reply.getByRole("button", { name: "Open in Web" }),
    ).toHaveCount(1);
  });
});
