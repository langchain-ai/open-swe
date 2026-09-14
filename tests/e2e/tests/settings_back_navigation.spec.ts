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

  await page.getByRole("button", { name: SAME_USER.login }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/my-settings$/);

  const backLink = page.getByRole("link", { name: "Back to app" });
  await expect(backLink).toHaveAttribute(
    "href",
    `${threadHref.pathname}${threadHref.search}${threadHref.hash}`,
  );
  await backLink.click();
  await expect(page).toHaveURL(threadUrl);
});
