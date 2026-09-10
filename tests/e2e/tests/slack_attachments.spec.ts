import { test, expect } from "@playwright/test";
import {
  SAME_USER,
  loginAs,
  threadIdFromUrl,
  threadState,
  waitForStateToContain,
} from "./helpers/dashboard";

// An image attached to a Slack message is fetched into the thread's sandbox at
// ingestion. The scripted agent then treats it as a file: it reads the pixel
// size off disk with a shell command, writes a copy, and views the copy with
// `read_file`. The web transcript renders the same image from the media endpoint.
test.describe("Slack attachments (real dashboard UI)", () => {
  test("an image sent in Slack lands in the sandbox, is worked on, and renders in the web transcript", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
    await page.locator("#attach-image").check();
    await page
      .locator("#text")
      .fill("<@U0BOT> E2E_IMAGE what are the dimensions of this image?");
    await page.locator("#send").click();

    // The size came from the file in /uploads, read with a shell command.
    await expect(
      page.locator(".msg.bot").filter({ hasText: "64x48 pixels" }),
    ).toBeVisible({ timeout: 60_000 });
    await expect(
      page.locator(".msg.bot").filter({ hasText: "-gradient-copy.png" }),
    ).toBeVisible();

    const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
    await webLink.click();
    await expect(page).toHaveURL(/\/agents\//);
    const threadId = threadIdFromUrl(page);

    await waitForStateToContain(page, threadId, "<media>");
    const state = await threadState(page, threadId);
    expect(state).toMatch(/\/uploads\/[0-9a-f]{64}-gradient\.png/);
    // The copy the agent made was read back through the multimodal read_file.
    expect(state).toMatch(/\/uploads\/[0-9a-f]{64}-gradient-copy\.png/);

    const image = page
      .getByTestId("user-message")
      .getByRole("img", { name: "gradient.png" })
      .first();
    await expect(image).toBeVisible();
    const src = await image.getAttribute("src");
    expect(src).toMatch(
      new RegExp(
        `/dashboard/api/threads/${threadId}/media/[0-9a-f]{64}-gradient\\.png$`,
      ),
    );
    await expect
      .poll(() =>
        image.evaluate((element) => (element as HTMLImageElement).naturalWidth),
      )
      .toBe(64);
  });
});
