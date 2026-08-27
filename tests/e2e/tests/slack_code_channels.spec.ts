import { test, expect } from "@playwright/test";

test.describe("Slack Code Channels", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
  });

  test("the agent promotes a task and keeps unmentioned follow-ups in one session", async ({
    page,
  }) => {
    const initialSend = page.waitForResponse(
      (response) =>
        response.url().endsWith("/mock/slack/send") &&
        response.request().method() === "POST",
    );
    await page.locator("#send").click();
    const initialResult = (await (await initialSend).json()) as {
      thread_id?: string;
    };

    const codeChannel = page
      .locator(".channel")
      .filter({ hasText: "Investigate flaky CI failures" });
    await expect(codeChannel).toBeVisible();
    await expect(codeChannel).toHaveClass(/active/);
    await expect(page.locator("#channel-title")).toHaveText(
      "# Investigate flaky CI failures",
    );
    await expect(page.locator("#channel-subtitle")).toContainText(
      "one task, one session",
    );
    await expect(page.locator("#status")).toBeVisible();
    await expect(page.locator("#status-text")).toHaveText("active");
    await expect(page.locator("#chrome")).toContainText("fakeorg/demo");
    await expect(page.locator("#chrome")).toContainText("/create-pr");
    await expect(page.locator("#thread")).toContainText(
      "I created this code channel for the investigation",
    );

    await expect(page.locator("#mention")).not.toBeChecked();
    await expect(page.locator("#mention")).toBeDisabled();
    await page
      .locator("#text")
      .fill("What is the current status? E2E_CODE_CHANNEL_FOLLOWUP");
    const followupSend = page.waitForResponse(
      (response) =>
        response.url().endsWith("/mock/slack/send") &&
        response.request().method() === "POST",
    );
    await page.locator("#send").click();
    const followupResult = (await (await followupSend).json()) as {
      thread_id?: string;
    };

    expect(initialResult.thread_id).toBeTruthy();
    expect(followupResult.thread_id).toBe(initialResult.thread_id);
    await expect(page.locator("#thread")).toContainText(
      "this unmentioned follow-up reached the same Open SWE session",
    );
    await expect(page.locator(".channel").filter({ hasText: "✦" })).toHaveCount(
      1,
    );
    await page.screenshot({
      path: "screenshots/slack-code-channel.png",
      fullPage: true,
    });

    await page.request.post("/control/login", {
      data: { login: "alice", email: "alice@example.com" },
    });
    await page.goto(`/agents/${initialResult.thread_id}`);
    const codeChannelLink = page.getByRole("link", {
      name: "Open code channel",
    });
    await expect(codeChannelLink).toBeVisible();
    await expect(codeChannelLink).toHaveAttribute(
      "href",
      /https:\/\/slack\.com\/app_redirect\?channel=C_CODE_/,
    );
    await page.screenshot({
      path: "screenshots/web-code-channel-link.png",
      fullPage: true,
    });
  });
});
