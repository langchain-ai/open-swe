import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { SAME_USER, loginAs, waitForThreadIdle } from "./helpers/dashboard";

async function attachScreenshot(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.png`);
  await page.getByRole("main").screenshot({ path });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

// The model labels each code-mode call, and the timeline shows that label
// instead of a bare "Eval" row. The description never reaches the interpreter.
test("labels each eval call with the description the model gave it", async ({
  page,
}, testInfo) => {
  await loginAs(page, SAME_USER);
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  const send = await page.request.post("/mock/slack/send", {
    data: { text: "<@U0BOT> E2E_EVAL_DESCRIPTION how many files are there?" },
  });
  expect(send.ok()).toBeTruthy();
  const { thread_id: threadId } = (await send.json()) as { thread_id: string };
  await waitForThreadIdle(page, threadId);

  await page.goto(`/agents/${threadId}`);
  const worked = page.getByRole("button", {
    name: /^Worked(?: for .+)? · \d+ actions?$/,
  });
  await expect(worked.first()).toBeVisible({ timeout: 30_000 });
  for (const fold of await worked.all()) await fold.click();

  const count = page.getByRole("button", {
    name: /Eval\s*Count the files in the repository/,
  });
  const greeting = page.getByRole("button", {
    name: /Eval\s*Check the greeting the helper returns/,
  });
  await expect(count).toBeVisible();
  await expect(greeting).toBeVisible();
  await attachScreenshot(page, testInfo, "eval-descriptions");

  await count.click();
  await expect(count).toContainText("2");
  await expect(count).not.toContainText("display_description");
  await attachScreenshot(page, testInfo, "eval-description-expanded");
});
