import { test, expect, type Page, type TestInfo } from "@playwright/test";

import {
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
} from "./helpers/dashboard";

// The real agent writes a Markdown guide, an HTML deck and a CSV into its
// sandbox; the Files panel renders each through the signed raw-file route, read
// from that sandbox, with the deck sandboxed away from the dashboard's origin.
test.describe("file previews (real dashboard UI)", () => {
  test("renders Markdown, a sandboxed HTML deck and a CSV from the workspace", async ({
    page,
  }, testInfo) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAs(page, SAME_USER);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await page
      .locator("#text")
      .fill("<@U0BOT> E2E_FILE_PREVIEWS write the preview files");
    await page.locator("#send").click();

    const reply = page
      .locator(".msg.bot")
      .filter({ hasText: "The preview files are ready." });
    await expect(reply).toBeVisible({ timeout: 90_000 });
    await reply.locator('a[href*="/agents/"]').click();
    await expect(page).toHaveURL(/\/agents\//);
    await dismissOnboardingIfShown(page);

    await page.getByRole("button", { name: "Show panel" }).click();
    const files = page
      .getByLabel("Open a surface")
      .getByRole("button", { name: /Files/ });
    await expect(files).toBeVisible({ timeout: 60_000 });
    await files.click();

    await openFile(page, "guide.md");
    await expect(
      page.getByRole("heading", { name: "Preview guide" }),
    ).toBeVisible();
    const badge = page.getByAltText("Badge");
    await expect
      .poll(() =>
        badge.evaluate((image: HTMLImageElement) => image.naturalWidth),
      )
      .toBe(48);
    await snap(page, testInfo, "file-previews-markdown-light");
    // The app settles its theme when it loads, so dark mode is captured fresh.
    await page.emulateMedia({ colorScheme: "dark" });
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Preview guide" }),
    ).toBeVisible();
    await snap(page, testInfo, "file-previews-markdown-dark");
    await page.emulateMedia({ colorScheme: "light" });

    await page.getByRole("link", { name: "the deck" }).click();
    const frame = page.locator('iframe[title="site/index.html"]');
    await expect(frame).toHaveAttribute(
      "sandbox",
      "allow-scripts allow-downloads",
    );
    const deck = page.frameLocator('iframe[title="site/index.html"]');
    await expect(deck.locator("#status")).toHaveText("module loaded");
    await expect(deck.locator("h1")).toHaveCSS("color", "rgb(124, 58, 237)");
    await snap(page, testInfo, "file-previews-html");

    await page.getByRole("button", { name: "Source", exact: true }).click();
    await expect(page.getByText("Quarterly deck").first()).toBeVisible();
    await expect(frame).toHaveCount(0);
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(frame).toBeVisible();

    await openFile(page, "scores.csv");
    await expect(page.getByRole("columnheader")).toHaveText([
      "region",
      "revenue",
    ]);
    await expect(page.getByRole("cell", { name: "95,5" })).toBeVisible();
    await snap(page, testInfo, "file-previews-csv");

    await openFile(page, "book.xlsx");
    await expect(page.getByRole("columnheader")).toHaveText([
      "region",
      "revenue",
    ]);
    await expect(page.getByRole("cell", { name: "120" })).toBeVisible();

    // One cell 100 million rows down, which unbounded padding takes past the heap.
    await openFile(page, "bomb.xlsx");
    await expect(
      page.getByText("This workbook is too large to preview."),
    ).toBeVisible();
    await expect(
      page.getByRole("searchbox", { name: "Search files" }),
    ).toBeEditable();
  });
});

async function openFile(page: Page, name: string) {
  const search = page.getByRole("searchbox", { name: "Search files" });
  await search.fill(name);
  await page.getByRole("treeitem", { name, exact: true }).click();
  await search.fill("");
}

async function snap(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path });
  await testInfo.attach(name, { path, contentType: "image/png" });
}
