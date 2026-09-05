import { test, expect, type Page } from "@playwright/test";
import {
  SAME_USER,
  loginAs,
  threadIdFromUrl,
  threadState,
  typeIntoComposer,
  waitForStateToContain,
} from "./helpers/dashboard";

// A 1x1 PNG. Small enough to compare byte-for-byte with what the server serves.
const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

// A fresh user lands on the default-model dialog; the composer sits behind it.
async function openNewThreadComposer(page: Page) {
  await loginAs(page, SAME_USER);
  await page.goto("/agents");
  const dismissOnboarding = page.getByRole("button", { name: "Maybe later" });
  await expect(dismissOnboarding).toBeVisible();
  await dismissOnboarding.click();
  await expect(dismissOnboarding).toBeHidden();
}

// An attached image is stored in the thread's sandbox at ingestion. The thread
// state carries only a reference to it, and the transcript renders it from the
// media endpoint rather than from bytes embedded in the message.
test.describe("dashboard attachments (real dashboard UI)", () => {
  test("stores an attached image in the sandbox and renders it from there", async ({
    page,
  }) => {
    await openNewThreadComposer(page);

    await page
      .locator('input[type="file"]')
      .first()
      .setInputFiles({
        name: "pixel.png",
        mimeType: "image/png",
        buffer: Buffer.from(PNG_BASE64, "base64"),
      });
    await expect(page.getByRole("img", { name: "pixel.png" })).toBeVisible();

    const prompt = "what is in this screenshot?";
    await typeIntoComposer(page, prompt);
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    const threadId = threadIdFromUrl(page);

    // Persisted state holds the reference, never the bytes.
    await waitForStateToContain(page, threadId, "<media>");
    const state = await threadState(page, threadId);
    expect(state).toContain("/uploads/");
    expect(state).not.toContain(PNG_BASE64);

    // A cold load renders the image from the server, not from the optimistic
    // copy the composer kept.
    await page.reload();
    const userMessage = page
      .getByTestId("user-message")
      .filter({ hasText: prompt });
    const image = userMessage.getByRole("img", { name: "pixel.png" });
    await expect(image).toBeVisible();
    const src = await image.getAttribute("src");
    expect(src).toMatch(
      new RegExp(
        `/dashboard/api/threads/${threadId}/media/[0-9a-f]{64}-pixel\\.png$`,
      ),
    );
    await expect
      .poll(() =>
        image.evaluate((element) => (element as HTMLImageElement).naturalWidth),
      )
      .toBeGreaterThan(0);

    const served = await page.request.get(src ?? "");
    expect(served.ok()).toBeTruthy();
    expect(served.headers()["content-type"]).toContain("image/png");
    expect(served.headers()["cache-control"]).toContain("immutable");
    expect((await served.body()).toString("base64")).toBe(PNG_BASE64);
  });

  test("refuses a media path that is not a stored attachment", async ({
    page,
  }) => {
    await openNewThreadComposer(page);
    await typeIntoComposer(page, "no attachment on this one");
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    const threadId = threadIdFromUrl(page);

    const traversal = await page.request.get(
      `/dashboard/api/threads/${threadId}/media/..%2Fetc%2Fpasswd`,
    );
    expect(traversal.status()).toBe(404);
    const missing = await page.request.get(
      `/dashboard/api/threads/${threadId}/media/${"0".repeat(64)}.png`,
    );
    expect(missing.status()).toBe(404);
  });
});
