import { test, expect, type Page } from "@playwright/test";
import {
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
  typeIntoComposer,
  waitForThreadIdle,
} from "./helpers/dashboard";

// The browser notification a finished run raises, end to end: the real agent
// records its answer, the real thread list carries it, and the real sidebar
// turns it into the notification. Only the OS notification itself is stubbed.
interface ShownNotification {
  title: string;
  body: string;
  requireInteraction: boolean;
  tag: string;
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const shown: Array<{
      title: string;
      body: string;
      requireInteraction: boolean;
      tag: string;
    }> = [];
    class FakeNotification {
      static permission = "granted";
      static requestPermission = async () => "granted";
      onclick: (() => void) | null = null;
      constructor(title: string, options: NotificationOptions = {}) {
        shown.push({
          title,
          body: options.body ?? "",
          requireInteraction: options.requireInteraction ?? false,
          tag: options.tag ?? "",
        });
      }
      close() {}
    }
    Object.assign(window, {
      Notification: FakeNotification,
      __notifications: shown,
    });
    localStorage.setItem("open-swe-notifications-enabled", "true");
  });
  await loginAs(page, SAME_USER);
  await page.request.post("/control/reset");
});

function shownNotifications(page: Page): Promise<Array<ShownNotification>> {
  return page.evaluate(
    () =>
      (window as typeof window & { __notifications: Array<ShownNotification> })
        .__notifications,
  );
}

async function notificationFor(
  page: Page,
  threadId: string,
): Promise<ShownNotification> {
  let found: ShownNotification | undefined;
  await expect
    .poll(
      async () => {
        found = (await shownNotifications(page)).find(
          (n) => n.tag === `run-${threadId}`,
        );
        return found !== undefined;
      },
      { timeout: 60_000, intervals: [500] },
    )
    .toBe(true);
  return found as ShownNotification;
}

async function sendSlackMessage(page: Page, text: string): Promise<string> {
  const send = await page.request.post("/mock/slack/send", { data: { text } });
  expect(send.ok()).toBeTruthy();
  return ((await send.json()) as { thread_id: string }).thread_id;
}

test.describe("run-completion notifications", () => {
  test("says what a Slack run answered and which PR it opened", async ({
    page,
  }) => {
    const threadId = await sendSlackMessage(
      page,
      "<@U0BOT> E2E_BUSY_HOLD:8 please add a greet() helper and open a PR",
    );
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);

    const notification = await notificationFor(page, threadId);
    expect(notification.body).toMatch(
      /^Opened PR #\d+( \(\+\d+ −\d+\))?\n✅ Done! I implemented the change and opened a PR: Add greet\(\) helper • Added greet\.py/,
    );
    expect(notification.body).not.toMatch(/[`*<>]/);
    expect(notification.requireInteraction).toBe(false);
  });

  test("flags a run waiting on a decision and stays quiet for a run that chose not to reply", async ({
    page,
  }) => {
    const silentId = await sendSlackMessage(
      page,
      "<@U0BOT> E2E_NOTIFY_SILENT E2E_BUSY_HOLD:6 just thinking out loud",
    );
    const choiceId = await sendSlackMessage(
      page,
      "<@U0BOT> E2E_NOTIFY_CHOICE E2E_BUSY_HOLD:10 ship the greeting?",
    );
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);

    const notification = await notificationFor(page, choiceId);
    expect(notification.body).toBe(
      "Needs your decision\nShould I ship the greeting change now? See the plan.",
    );
    expect(notification.requireInteraction).toBe(true);

    await waitForThreadIdle(page, silentId);
    expect((await shownNotifications(page)).map((n) => n.tag)).not.toContain(
      `run-${silentId}`,
    );
  });

  test("shows a web run's final answer", async ({ page }) => {
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    await typeIntoComposer(
      page,
      "E2E_NOTIFY_WEB E2E_BUSY_HOLD:8 where does greet() live?",
    );
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    const threadId = new URL(page.url()).pathname.split("/").pop() as string;
    // A notification is for a thread you are not looking at.
    await page.goto("/agents");

    const notification = await notificationFor(page, threadId);
    expect(notification.body).toBe(
      "The greeting lives in greet.py, see the file.",
    );
  });
});
