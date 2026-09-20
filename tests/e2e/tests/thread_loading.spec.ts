import { test, expect } from "@playwright/test";
import {
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
  openThreadViaSlackLink,
  threadIdFromUrl,
  waitForThreadIdle,
} from "./helpers/dashboard";

test("revisits a cloud thread after the old idle timeout without rehydrating", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await openThreadViaSlackLink(page);
  const threadId = threadIdFromUrl(page);
  await waitForThreadIdle(page, threadId);
  const reply = page.getByRole("link", { name: "Add greet() helper" }).first();
  await expect(reply).toBeVisible();
  await page.clock.install();
  let hydrations = 0;
  page.on("request", (request) => {
    if (
      new URL(request.url()).pathname ===
      `/dashboard/api/threads/${threadId}/state`
    )
      hydrations++;
  });
  await page.getByRole("link", { name: "New Thread", exact: true }).click();
  await expect(page).toHaveURL(/\/agents\/?$/);
  await dismissOnboardingIfShown(page);
  await page.clock.runFor(80_000);
  await page.locator(`a[href="/agents/${threadId}"]`).first().click();
  await expect(reply).toBeVisible();
  if (process.env.THREAD_LOADING_SCREENSHOT) {
    await page.screenshot({
      path: process.env.THREAD_LOADING_SCREENSHOT,
      fullPage: true,
    });
  }
  expect(hydrations).toBe(0);
});
