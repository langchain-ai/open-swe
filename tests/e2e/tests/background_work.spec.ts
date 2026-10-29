import { test, expect } from "@playwright/test";

import {
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
} from "./helpers/dashboard";

// The real agent starts a `background_execute` command and a `monitor` in its
// (local-provider) sandbox; the thread's right panel lists both in its
// Background work surface, read live from the sandbox through the dashboard API.
test.describe("background work panel (real dashboard UI)", () => {
  test("lists the running monitor and background command", async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAs(page, SAME_USER);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await page
      .locator("#text")
      .fill(
        "<@U0BOT> E2E_BACKGROUND_WORK build the assets and watch the dev server log",
      );
    await page.locator("#send").click();

    const reply = page
      .locator(".msg.bot")
      .filter({ hasText: "Starting the background work now." });
    await expect(reply).toBeVisible();
    await reply.locator('a[href*="/agents/"]').click();
    await expect(page).toHaveURL(/\/agents\//);
    await dismissOnboardingIfShown(page);

    await page.getByRole("button", { name: "Show panel" }).click();
    // The tools may still be starting when the page opens: the surface becomes
    // available when the backend invalidates the thread's background work.
    const launcher = page
      .getByLabel("Open a surface")
      .getByRole("button", { name: /Background work/ });
    await expect(launcher).toBeVisible({ timeout: 60_000 });
    await launcher.click();
    const panel = page.getByTestId("background-work-panel");
    await expect(panel).toBeVisible();
    const monitors = panel.getByRole("region", { name: "Monitors" });
    const commands = panel.getByRole("region", { name: "Commands" });
    await expect(monitors).toContainText("errors in the dev server log", {
      timeout: 60_000,
    });
    await expect(monitors).toContainText('echo "GET /health 200');
    await expect(commands).toContainText("echo 'building assets'; sleep 110", {
      timeout: 60_000,
    });
    await commands.getByRole("button", { expanded: false }).first().click();
    await expect(commands.getByLabel("Latest output")).toContainText(
      "building assets",
    );
    await monitors.getByRole("button", { expanded: false }).first().click();
    await expect(monitors.getByLabel("Latest output")).toContainText(
      /^GET \/health 200/,
      { timeout: 30_000 },
    );

    const pagePath = testInfo.outputPath("background-work-thread.png");
    await page.screenshot({ path: pagePath });
    await testInfo.attach("background-work-thread", {
      path: pagePath,
      contentType: "image/png",
    });
    const panelPath = testInfo.outputPath("background-work-panel.png");
    await panel.screenshot({ path: panelPath });
    await testInfo.attach("background-work-panel", {
      path: panelPath,
      contentType: "image/png",
    });
  });
});
