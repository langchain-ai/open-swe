import { expect, test } from "@playwright/test";

import {
  loginAs,
  openThreadViaSlackLink,
  SAME_USER,
} from "./helpers/dashboard";

test("Back to app returns to the thread that opened settings", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await openThreadViaSlackLink(page);
  const threadUrl = page.url();
  const threadHref = new URL(threadUrl);

  const userMenu = page.getByRole("button", { name: SAME_USER.login });
  const settingsItem = page.getByRole("menuitem", { name: "Settings" });
  await expect(async () => {
    if (!(await settingsItem.isVisible())) await userMenu.click();
    await expect(settingsItem).toBeVisible();
  }).toPass();
  await settingsItem.click();
  await expect(page).toHaveURL(/\/my-settings$/);

  const backLink = page.getByRole("link", { name: "Back to app" });
  await expect(backLink).toHaveAttribute(
    "href",
    `${threadHref.pathname}${threadHref.search}${threadHref.hash}`,
  );
  await backLink.click();
  await expect(page).toHaveURL(threadUrl);
});
