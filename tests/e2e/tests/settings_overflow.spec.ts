import { expect, test } from "@playwright/test";

import { loginAs, SAME_USER } from "./helpers/dashboard";

for (const viewport of [
  { width: 1512, height: 850 },
  { width: 390, height: 844 },
]) {
  test(`admin scroll stays inside the content pane at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await loginAs(page, SAME_USER);
    await page.goto("/admin");
    await expect(
      page.getByRole("heading", { name: "Users", exact: true }),
    ).toBeAttached();
    await expect(page.locator("#policy-enabled")).toBeAttached();

    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollHeight))
      .toBe(viewport.height);

    await page.locator("main").evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect(
      page.getByRole("heading", { name: "Users", exact: true }),
    ).toBeVisible();
    await expect
      .poll(() => page.locator("main").evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);

    await page.evaluate(() =>
      window.scrollTo(0, document.documentElement.scrollHeight),
    );
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
  });
}
