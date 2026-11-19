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
