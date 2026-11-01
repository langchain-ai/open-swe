import { test, expect, type Page } from "@playwright/test";
import {
  loginAs,
  SAME_USER,
  OTHER_USER,
  seedOpenPullRequest,
} from "./helpers/dashboard";

class Notifications {
  constructor(readonly page: Page) {}

  async create(title: string, key = crypto.randomUUID()) {
    const response = await this.page.request.post("/control/notifications", {
      data: { key, title, recipient: "bob" },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
  }

  async fallback() {
    const response = await this.page.request.post(
      "/control/notifications/fallback",
    );
    expect(response.ok(), await response.text()).toBeTruthy();
  }

  async dms() {
    const response = await this.page.request.get(
      "/mock/slack/messages?channel=D_BOB",
    );
    expect(response.ok()).toBeTruthy();
    return (await response.json()) as Array<{ text: string }>;
  }

  async pending() {
    const response = await this.page.request.get(
      "/dashboard/api/notifications",
    );
    expect(response.ok()).toBeTruthy();
    return (await response.json()) as Array<{
      id: string;
      title: string;
      url: string;
    }>;
  }
}

// The OS notification center is outside the browser, so the spec stands in for it:
// a granted Notification that records each one and reports it shown, or never does.
class DesktopNotifications {
  constructor(private readonly page: Page) {}

  static async install(page: Page, shows = true) {
    await page.addInitScript((confirm) => {
      localStorage.setItem("open-swe-notifications-enabled", "true");
      const shown: Array<{ title: string; body: string; tag: string }> = [];
      const clicks: Array<() => void> = [];
      Object.assign(window, {
        __desktopNotifications: shown,
        __clickDesktopNotification: (index: number) => clicks[index]?.(),
      });
      class FakeNotification {
        static permission = "granted";
        static requestPermission = async () => "granted";
        onshow: (() => void) | null = null;
        onclick: (() => void) | null = null;
        constructor(title: string, options: NotificationOptions) {
          shown.push({
            title,
            body: options.body ?? "",
            tag: options.tag ?? "",
          });
          clicks.push(() => this.onclick?.());
          if (confirm) setTimeout(() => this.onshow?.(), 0);
        }
        close() {}
      }
      Object.defineProperty(window, "Notification", {
        value: FakeNotification,
      });
    }, shows);
    return new DesktopNotifications(page);
  }

  async titles() {
    return this.page.evaluate(() =>
      (
        window as unknown as {
          __desktopNotifications: Array<{ title: string }>;
        }
      ).__desktopNotifications.map((n) => n.title),
    );
  }

  async click(index: number) {
    await this.page.evaluate(
      (position) =>
        (
          window as unknown as {
            __clickDesktopNotification: (index: number) => void;
          }
        ).__clickDesktopNotification(position),
      index,
    );
  }
}

test("general notifications show on the desktop and confirmed delivery suppresses the DM", async ({
  page,
}) => {
  const desktop = await DesktopNotifications.install(page);
  await loginAs(page, OTHER_USER);
  await page.goto("/agents", { waitUntil: "domcontentloaded" });
  const notifications = new Notifications(page);
  const title = `App notification ${crypto.randomUUID()}`;
  const key = crypto.randomUUID();
  await notifications.create(title, key);
  await expect.poll(() => desktop.titles()).toContain(title);
  await expect
    .poll(async () =>
      (await notifications.pending()).some((n) => n.title === title),
    )
    .toBe(false);
  await page.reload({ waitUntil: "domcontentloaded" });
  await notifications.create(title, key);
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  expect(await desktop.titles()).toEqual([]);
  await notifications.fallback();
  expect(
    (await notifications.dms()).filter((n) => n.text === title),
  ).toHaveLength(0);
});

test("offline or unconfirmed desktop delivery falls back to one DM and receipts are recipient scoped", async ({
  page,
  browser,
}) => {
  await loginAs(page, OTHER_USER);
  const notifications = new Notifications(page);
  const offline = `Offline notification ${crypto.randomUUID()}`;
  const key = crypto.randomUUID();
  await notifications.create(offline, key);
  await notifications.create(offline, key);
  expect(
    (await notifications.pending()).filter((n) => n.title === offline),
  ).toHaveLength(1);
  const entry = (await notifications.pending()).find(
    (n) => n.title === offline,
  )!;
  expect(entry).toBeDefined();
  const alice = await browser.newPage();
  await loginAs(alice, SAME_USER);
  expect(await new Notifications(alice).pending()).not.toContainEqual(
    expect.objectContaining({ id: entry.id }),
  );
  const denied = await alice.request.post(
    `/dashboard/api/notifications/${entry.id}/delivered`,
    {
      headers: { origin: new URL(test.info().project.use.baseURL!).origin },
    },
  );
  expect(denied.status()).toBe(404);
  await alice.close();
  await notifications.fallback();
  await notifications.fallback();
  expect(
    (await notifications.dms()).filter((n) => n.text === offline),
  ).toHaveLength(1);

  const desktop = await DesktopNotifications.install(page, false);
  await page.goto("/agents", { waitUntil: "domcontentloaded" });
  const unconfirmed = `Unconfirmed notification ${crypto.randomUUID()}`;
  await notifications.create(unconfirmed);
  await expect.poll(() => desktop.titles()).toContain(unconfirmed);
  expect(
    (await notifications.pending()).some((n) => n.title === unconfirmed),
  ).toBe(true);
  await notifications.fallback();
  expect(
    (await notifications.dms()).filter((n) => n.text === unconfirmed),
  ).toHaveLength(1);
});

test("queued and steered mentions notify once per accepted message and skip self mentions", async ({
  page,
  browser,
}) => {
  await loginAs(page, SAME_USER);
  const recipient = await browser.newPage();
  await loginAs(recipient, OTHER_USER);
  await page.goto("/agents", { waitUntil: "domcontentloaded" });
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially("E2E_BUSY_HOLD:20 inspect the code");
  await editor.press("Enter");
  await expect(page).toHaveURL(/\/agents\/[a-f0-9-]+$/);
  const threadId = new URL(page.url()).pathname.split("/").pop()!;
  for (const strategy of ["interrupt", "enqueue"]) {
    const response = await page.request.post(
      `/dashboard/api/threads/${threadId}/commands`,
      {
        headers: { origin: new URL(page.url()).origin },
        data: {
          id: crypto.randomUUID(),
          method: "run.start",
          params: {
            multitask_strategy: strategy,
            input: {
              messages: [
                {
                  id: crypto.randomUUID(),
                  type: "human",
                  content: "Please ask <@W123BOB> <@W123BOB> <@W123ALICE>",
                },
              ],
            },
          },
        },
      },
    );
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  await expect
    .poll(
      async () =>
        (await new Notifications(recipient).pending()).filter((n) =>
          n.url.endsWith(threadId),
        ).length,
    )
    .toBe(2);
  const own = await new Notifications(page).pending();
  expect(own.filter((n) => n.url.endsWith(threadId))).toHaveLength(0);
  await new Notifications(page).fallback();
  expect(
    (await new Notifications(page).dms()).filter((n) =>
      n.text.includes(threadId),
    ),
  ).toHaveLength(2);
  await recipient.close();
});

for (const review of [false, true]) {
  test(`Slack-name mentions in ${review ? "Review" : "workspace"} chat notify the recipient with a desktop notification`, async ({
    page,
    browser,
  }) => {
    await loginAs(page, SAME_USER);
    const beforeDms = (await new Notifications(page).dms()).length;
    const recipient = await browser.newPage();
    const desktop = await DesktopNotifications.install(recipient);
    await loginAs(recipient, OTHER_USER);
    await recipient.goto("/agents", { waitUntil: "domcontentloaded" });
    if (review) {
      const pr = await seedOpenPullRequest(page, {
        title: `Mention review ${crypto.randomUUID()}`,
      });
      await page.goto(`/agents/reviews/fakeorg/demo/${pr.number}`, {
        waitUntil: "domcontentloaded",
      });
      await page.getByRole("tab", { name: "Chat", exact: true }).click();
    } else {
      await page.goto("/agents", { waitUntil: "domcontentloaded" });
    }
    const editor = page.getByTestId("composer-editor");
    await expect(editor).toBeVisible();
    await editor.click();
    await editor.pressSequentially("Please ask @bob");
    await page.getByRole("option", { name: /@Bob @bob/ }).click();
    await expect(editor).toContainText("@Bob");
    await editor.pressSequentially(" about this change");
    const notificationReceived = recipient.waitForResponse(
      (response) =>
        response.url().includes("/notifications/") &&
        response.url().endsWith("/delivered") &&
        response.ok(),
    );
    await editor.press("Enter");
    await expect
      .poll(() => desktop.titles())
      .toContain("alice mentioned you in Open SWE");
    await notificationReceived;
    await desktop.click(0);
    await expect(recipient).toHaveURL(/\/agents\/[0-9a-f-]{36}$/);
    await new Notifications(page).fallback();
    const dms = await new Notifications(page).dms();
    expect(
      dms
        .slice(beforeDms)
        .filter((n) => n.text.includes("alice mentioned you in Open SWE")),
    ).toHaveLength(0);
    await recipient.close();
  });
}

test("Escape dismisses empty mention suggestions", async ({ page }) => {
  await loginAs(page, SAME_USER);
  await page.goto("/agents", { waitUntil: "domcontentloaded" });
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially("@no-matching-person-or-file");
  await expect(page.getByText("No matching people or files.")).toBeVisible();
  await editor.press("Escape");
  await expect(page.getByRole("listbox")).toHaveCount(0);
});

test("without desktop notification permission the mention falls back to Slack", async ({
  page,
}) => {
  await page.addInitScript(() => {
    class DeniedNotification {
      static permission = "denied";
      static requestPermission = async () => "denied";
      constructor() {
        throw new Error("denied");
      }
    }
    Object.defineProperty(window, "Notification", {
      value: DeniedNotification,
    });
  });
  await loginAs(page, OTHER_USER);
  await page.goto("/agents", { waitUntil: "domcontentloaded" });
  const notifications = new Notifications(page);
  const title = `Denied notification ${crypto.randomUUID()}`;
  await notifications.create(title);
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  expect((await notifications.pending()).some((n) => n.title === title)).toBe(
    true,
  );
  await notifications.fallback();
  expect(
    (await notifications.dms()).filter((n) => n.text === title),
  ).toHaveLength(1);
});
