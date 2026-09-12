import { expect, test, type Page } from "@playwright/test";

import { SAME_USER, loginAs } from "../helpers/dashboard";

type HeldRequest = {
  release: () => void;
  started: Promise<void>;
};

async function holdRequest(
  page: Page,
  url: string,
  fetchBeforeRelease: boolean,
): Promise<HeldRequest> {
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let markStarted = () => {};
  const started = new Promise<void>((resolve) => {
    markStarted = resolve;
  });
  await page.route(url, async (route) => {
    if (fetchBeforeRelease) {
      const response = await route.fetch();
      markStarted();
      await gate;
      await route.fulfill({ response });
      return;
    }
    markStarted();
    await gate;
    await route.continue();
  });
  return { release, started };
}

async function typeDraft(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
}

test("a follow-up overtaken by interruption survives a cold reload exactly once", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await page.request.post("/control/reset");
  const send = await page.request.post("/mock/slack/send", {
    data: {
      text: "<@U0BOT> E2E_BUSY_HOLD:30 exercise stop reload follow-up",
    },
  });
  expect(send.ok()).toBeTruthy();
  const { thread_id: threadId } = (await send.json()) as {
    thread_id: string;
  };

  await page.goto(`/agents/${threadId}`);
  const stop = page.getByRole("button", { name: "Stop run" });
  await expect(stop).toBeVisible();
  const queued = await holdRequest(
    page,
    `**/dashboard/api/threads/${threadId}/messages`,
    false,
  );
  const cancellation = await holdRequest(
    page,
    `**/dashboard/api/threads/${threadId}/cancel`,
    true,
  );
  const followUp = `Follow-up overlapping interruption ${Date.now()}`;
  await typeDraft(page, followUp);
  await page.getByTestId("composer-editor").press("Enter");
  await queued.started;
  await stop.click();
  await cancellation.started;

  queued.release();
  cancellation.release();
  await page.reload();

  await expect(
    page.getByTestId(/^(user|queued)-message$/).filter({ hasText: followUp }),
  ).toHaveCount(1);
  await expect(stop).toHaveCount(0);
});
