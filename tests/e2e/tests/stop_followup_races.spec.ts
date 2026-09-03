import { expect, test, type Page } from "@playwright/test";

import { SAME_USER, loginAs, waitForThreadNotBusy } from "./helpers/dashboard";

const HELD_RUN = "<@U0BOT> E2E_BUSY_HOLD:30 reproduce stop and follow-up races";
const PHASE_HELD_RUN =
  "<@U0BOT> E2E_PHASE_HOLD:after-tool:2:30 reproduce queue persistence races";

type HeldRequest = {
  release: () => void;
  started: Promise<void>;
};

type HeldCancellation = {
  release: () => void;
  serverResponded: Promise<void>;
};

async function openHeldRun(page: Page, message = HELD_RUN): Promise<string> {
  await loginAs(page, SAME_USER);
  const reset = await page.request.post("/control/reset");
  expect(reset.ok()).toBeTruthy();
  const send = await page.request.post("/mock/slack/send", {
    data: { text: message },
  });
  expect(send.ok()).toBeTruthy();
  const { thread_id: threadId } = (await send.json()) as {
    thread_id: string;
  };
  await page.goto(`/agents/${threadId}`);
  await expect(page.getByRole("button", { name: "Stop run" })).toBeVisible();
  return threadId;
}

async function holdCancellation(
  page: Page,
  threadId: string,
): Promise<HeldCancellation> {
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let markServerResponded = () => {};
  const serverResponded = new Promise<void>((resolve) => {
    markServerResponded = resolve;
  });
  await page.route(
    `**/dashboard/api/threads/${threadId}/cancel`,
    async (route) => {
      const response = await route.fetch();
      markServerResponded();
      await gate;
      await route.fulfill({ response });
    },
  );
  return { release, serverResponded };
}

async function holdQueueMessage(
  page: Page,
  threadId: string,
): Promise<HeldRequest> {
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let markStarted = () => {};
  const started = new Promise<void>((resolve) => {
    markStarted = resolve;
  });
  await page.route(
    `**/dashboard/api/threads/${threadId}/messages`,
    async (route) => {
      markStarted();
      await gate;
      await route.continue();
    },
  );
  return { release, started };
}

async function typeDraft(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
}

async function raceQueuedSendAgainstStop(
  page: Page,
  threadId: string,
  followUp: string,
  stopWith: "click" | "escape",
) {
  const queue = await holdQueueMessage(page, threadId);
  const cancellation = await holdCancellation(page, threadId);
  await typeDraft(page, followUp);
  await page.getByTestId("composer-editor").press("Enter");
  await queue.started;

  if (stopWith === "click") {
    await page.getByRole("button", { name: "Stop run" }).click();
  } else {
    await page.keyboard.press("Escape");
  }
  await cancellation.serverResponded;
  await waitForThreadNotBusy(page, threadId);
  const messageResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `/dashboard/api/threads/${threadId}/messages`,
  );
  queue.release();
  const response = await messageResponse;
  await page.waitForTimeout(250);
  cancellation.release();
  await expect(
    page.getByTestId("queued-message").filter({ hasText: followUp }),
  ).toHaveCount(0);
  expect(response.status()).toBe(409);
}

test.describe("stop followed by new input", () => {
  test.setTimeout(45_000);

  for (const stopWith of ["click", "escape"] as const) {
    test(`keeps a follow-up visible after refresh when ${stopWith}-to-stop overtakes its queue request`, async ({
      page,
    }) => {
      const threadId = await openHeldRun(page);
      const followUp = `Keep the ${stopWith} follow-up after refresh.`;
      await raceQueuedSendAgainstStop(page, threadId, followUp, stopWith);
      await page.reload();

      await expect(
        page.getByTestId("user-message").filter({ hasText: followUp }),
      ).toBeVisible();
    });
  }

  test("does not drop a follow-up whose queue request overlaps click-to-stop", async ({
    page,
  }) => {
    const threadId = await openHeldRun(page);
    const followUp = "Keep the click-overlapping replacement instruction.";
    await raceQueuedSendAgainstStop(page, threadId, followUp, "click");

    await expect(page.getByTestId("composer-editor")).toContainText(followUp);
  });

  test("does not drop a follow-up whose queue request overlaps Escape-to-stop", async ({
    page,
  }) => {
    const threadId = await openHeldRun(page);
    const followUp = "Keep the Escape-overlapping replacement instruction.";
    await raceQueuedSendAgainstStop(page, threadId, followUp, "escape");

    await expect(page.getByTestId("composer-editor")).toContainText(followUp);
  });

  test("keeps a queued follow-up visible after reloading a busy thread", async ({
    page,
  }) => {
    await openHeldRun(page, PHASE_HELD_RUN);
    const followUp = "Keep this queued instruction visible across reload.";
    await typeDraft(page, followUp);
    await page.getByTestId("composer-editor").press("Enter");
    await expect(
      page.getByTestId("queued-message").filter({ hasText: followUp }),
    ).toBeVisible();

    await page.reload();

    await expect(
      page.getByTestId("queued-message").filter({ hasText: followUp }),
    ).toBeVisible();
  });

  test("preserves an unsent draft when switching threads", async ({ page }) => {
    const threadId = await openHeldRun(page, PHASE_HELD_RUN);
    const otherRun = await page.request.post("/mock/slack/send", {
      data: { text: "<@U0BOT> create another thread for navigation" },
    });
    expect(otherRun.ok()).toBeTruthy();
    const { thread_id: otherThreadId } = (await otherRun.json()) as {
      thread_id: string;
    };
    const draft = "Keep this unsent draft while I inspect another thread.";
    await typeDraft(page, draft);

    await page.goto(`/agents/${otherThreadId}`);
    await page.goto(`/agents/${threadId}`);

    await expect(page.getByTestId("composer-editor")).toContainText(draft);
  });

  test("shows a queued follow-up in another client viewing the busy thread", async ({
    page,
    browser,
  }) => {
    const threadId = await openHeldRun(page, PHASE_HELD_RUN);
    const otherContext = await browser.newContext();
    const otherPage = await otherContext.newPage();
    await loginAs(otherPage, SAME_USER);
    await otherPage.goto(`/agents/${threadId}`);
    await expect(
      otherPage.getByRole("button", { name: "Stop run" }),
    ).toBeVisible();
    const followUp = "Mirror this queued instruction to the other client.";

    await typeDraft(page, followUp);
    await page.getByTestId("composer-editor").press("Enter");

    await expect(
      otherPage.getByTestId("queued-message").filter({ hasText: followUp }),
    ).toBeVisible();
    await otherContext.close();
  });
});
