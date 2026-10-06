import { test, expect } from "@playwright/test";
import { loginAs, SAME_USER } from "./helpers/dashboard";

test("annotations intercept navigation and seed a new thread composer", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await page.goto("/agents");
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  if (process.env.ANNOTATION_SCREENSHOTS) {
    await page.screenshot({
      path: `${process.env.ANNOTATION_SCREENSHOTS}/before.png`,
    });
  }
  await page.keyboard.press("Control+Alt+Shift+A");
  await expect(
    page.getByRole("region", { name: "Page annotations" }),
  ).toBeVisible();
  const link = page.getByRole("link", { name: "Skills", exact: true }).first();
  await link.click();
  await expect(page).toHaveURL(/\/agents\/?$/);
  await page
    .getByRole("textbox", { name: "Annotation note" })
    .fill("Make this navigation easier to find");
  await page.getByRole("button", { name: "Save annotation" }).click();
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "Copy annotations" }).click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("Make this navigation easier to find");
  expect(copied).toContain('"selector"');
  if (process.env.ANNOTATION_SCREENSHOTS) {
    await page.screenshot({
      path: `${process.env.ANNOTATION_SCREENSHOTS}/after.png`,
    });
  }
  await page
    .getByRole("button", { name: "Start new thread", exact: true })
    .click();
  await expect(page.getByTestId("composer-editor")).toContainText(
    "Make this navigation easier to find",
  );
  await expect(page.getByTestId("composer-editor")).toContainText('"selector"');
  await expect(
    page.getByRole("region", { name: "Page annotations" }),
  ).toBeHidden();
});
