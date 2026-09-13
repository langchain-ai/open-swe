import { test, expect } from "@playwright/test";

import {
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
  typeIntoComposer,
} from "./helpers/dashboard";

const TITLE = "Iframe E2E Preview";

test.describe("show_user html", () => {
  test.skip(
    process.env.SANDBOX_TYPE !== "langsmith",
    "requires LangSmith sandbox download URLs",
  );

  test("renders and controls sandboxed HTML from a real tool artifact", async ({
    page,
  }) => {
    // `show_user` is wired only for dashboard/desktop runs, so this drives the
    // real dashboard composer rather than the Slack mock.
    await loginAs(page, SAME_USER);
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    await typeIntoComposer(page, "E2E_IFRAME render the preview");
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);

    const iframe = page.locator(`iframe[title="${TITLE}"]`);
    await expect(iframe).toBeVisible({ timeout: 60_000 });
    await expect(iframe).toHaveAttribute(
      "sandbox",
      "allow-scripts allow-downloads",
    );
    await expect(iframe).toHaveAttribute("allow", "clipboard-write");

    const preview = page.frameLocator(`iframe[title="${TITLE}"]`);
    await expect(preview.locator("#output-data")).toHaveText(
      "Prototype loaded",
    );
    await expect(preview.locator("body")).toHaveCSS(
      "color",
      "rgb(102, 51, 153)",
    );
    await expect
      .poll(() =>
        iframe.evaluate((element) => element.getBoundingClientRect().height),
      )
      .toBeGreaterThanOrEqual(420);

    const toggle = page.getByRole("button", { name: TITLE });
    await toggle.click();
    await expect(iframe).toHaveCount(0);
    await toggle.click();
    await expect(iframe).toBeVisible();
    await expect(preview.locator("#output-data")).toHaveText(
      "Prototype loaded",
    );

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download HTML" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("iframe-output.html");

    await expect(
      page.getByRole("button", { name: "Open in new tab" }),
    ).toHaveCount(0);
  });
});
