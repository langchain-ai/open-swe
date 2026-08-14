import { test, expect, type Page } from "@playwright/test";

const USER = { login: "alice", email: "alice@example.com" };
const TITLE = "Iframe E2E Preview";

async function login(page: Page) {
  const response = await page.request.post("/control/login", { data: USER });
  expect(response.ok()).toBeTruthy();
}

test.describe("output_iframe", () => {
  test("renders and controls sandboxed HTML from a real tool artifact", async ({
    page,
    context,
  }) => {
    await login(page);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await page.locator("#text").fill("<@U0BOT> E2E_IFRAME render the preview");
    await page.locator("#send").click();

    const reply = page
      .locator(".msg.bot")
      .filter({ hasText: "Preparing the iframe preview now." });
    await expect(reply).toBeVisible();
    const webLink = reply.locator('a[href*="/agents/"]');
    await expect(webLink).toBeVisible();
    await webLink.click();
    await expect(page).toHaveURL(/\/agents\//);

    const iframe = page.locator(`iframe[title="${TITLE}"]`);
    await expect(iframe).toBeVisible({ timeout: 60_000 });
    await expect(iframe).toHaveAttribute(
      "sandbox",
      "allow-scripts allow-downloads",
    );
    await expect(iframe).toHaveAttribute("allow", "clipboard-write");

    const preview = page.frameLocator(`iframe[title="${TITLE}"]`);
    await expect(preview.locator("#output-data")).toHaveText(
      "Bundled data loaded",
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
      "Bundled data loaded",
    );

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download HTML" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("iframe-output.html");

    const popupPromise = context.waitForEvent("page");
    await page.getByRole("button", { name: "Open in new tab" }).click();
    const popup = await popupPromise;
    await popup.waitForLoadState("domcontentloaded");
    const popupIframe = popup.locator("iframe");
    await expect(popupIframe).toHaveAttribute(
      "sandbox",
      "allow-scripts allow-downloads",
    );
    await expect(popupIframe).toHaveAttribute("allow", "clipboard-write");
    await expect(
      popup.frameLocator("iframe").locator("#output-data"),
    ).toHaveText("Bundled data loaded");
  });
});
