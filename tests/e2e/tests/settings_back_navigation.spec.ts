import { expect, test } from "@playwright/test";

import {
  expectTranscriptVisible,
  loginAs,
  openThreadViaSlackLink,
  SAME_USER,
} from "./helpers/dashboard";

test("Back to app returns to the thread that opened settings", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await openThreadViaSlackLink(page);
  // The link navigation resolves before hydration; wait for client content.
  await expectTranscriptVisible(page);
  const threadUrl = page.url();

  await page.getByRole("button", { name: SAME_USER.login }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/my-settings$/);

  await page.getByRole("button", { name: "Back to app" }).click();
  await expect(page).toHaveURL(threadUrl);
});

test("Settings Connections preserves Slack recovery and the main app has its own entry", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  const error = "Slack recovery expired or changed — connect Slack again";
  const query = new URLSearchParams({
    slack_recovery: "test-recovery-token",
    recovery_error: error,
  });
  await page.goto(`/my-settings/connections?${query}`);
  const dialog = page.getByRole("dialog", {
    name: "Recover your Robin account",
  });
  await expect(dialog.getByRole("alert")).toHaveText(error);
  await expect(page).toHaveURL(/\/my-settings\/connections$/);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("heading", { name: "Accounts" })).toBeVisible();
  await page.getByRole("button", { name: "Back to app" }).click();
  const connections = page.getByRole("link", {
    name: "Connections",
    exact: true,
  });
  await expect(connections).toHaveAttribute("href", "/agents/connections");
  await connections.click();
  await expect(page).toHaveURL(/\/agents\/connections$/);
  await expect(page.getByRole("heading", { name: "Accounts" })).toBeVisible();
  await expect(connections).toHaveAttribute("aria-current", "page");
});
