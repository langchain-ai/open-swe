import { test, expect, type Page } from "@playwright/test";

// Exercises the sidebar's "Copy sandbox ID" action against the REAL dashboard
// UI and a REAL (local-provider) sandbox: the Slack flow runs the agent, which
// creates a sandbox and stamps its id into the thread metadata, and the UI
// copies that same id. Only the LLM/GitHub/Slack boundaries are faked.
const SAME_USER = { login: "alice", email: "alice@example.com" };
const OTHER_USER = { login: "bob", email: "bob@example.com" };

async function loginAs(page: Page, user: { login: string; email: string }) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
}

// Drive the Slack flow so the real agent creates a thread + sandbox, then follow
// the bot's "Open in Web" link. Returns the created thread id.
async function createThreadWithSandbox(page: Page): Promise<string> {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page.locator("#text").fill("<@U0BOT> please add a greet() helper and open a PR");
  await page.locator("#send").click();
  await expect(page.locator(".msg.bot").filter({ hasText: "Add greet() helper" })).toBeVisible();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);

  const id = new URL(page.url()).pathname.split("/").filter(Boolean).pop();
  expect(id).toBeTruthy();
  return id as string;
}

const copyItem = (page: Page) => page.getByRole("menuitem", { name: "Copy sandbox ID" });

test.describe("thread sandbox id (real dashboard UI)", () => {
  test("desktop: context menu copies the real sandbox id", async ({ page, baseURL }) => {
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
      origin: baseURL,
    });
    await loginAs(page, SAME_USER);
    const threadId = await createThreadWithSandbox(page);

    const row = page.locator(`a[href$="/agents/${threadId}"]`).first();
    await expect(row).toBeVisible();
    await row.click({ button: "right" });

    await expect(copyItem(page)).toBeEnabled();
    await copyItem(page).click();

    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip.length).toBeGreaterThan(0);
  });

  test("iPad: long press copies the sandbox id without navigating", async ({
    browser,
    baseURL,
  }) => {
    // 834px is wider than the 767px mobile breakpoint, so the sidebar renders
    // inline while Chromium still emits touch events for the long press.
    const context = await browser.newContext({
      baseURL,
      viewport: { width: 834, height: 1112 },
      isMobile: true,
      hasTouch: true,
    });
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
      origin: baseURL,
    });
    const page = await context.newPage();
    await loginAs(page, SAME_USER);

    // Two threads: we sit on B's page and act on A's sidebar row, so a stray
    // Link navigation would be observable as a URL change to A.
    const threadA = await createThreadWithSandbox(page);
    const threadB = await createThreadWithSandbox(page);
    await expect(page).toHaveURL(new RegExp(`/agents/${threadB}$`));

    const rowA = page.locator(`a[href$="/agents/${threadA}"]`).first();
    await expect(rowA).toBeVisible();

    await rowA.evaluate((element) => {
      const touch = new Touch({
        identifier: 0,
        target: element,
        clientX: 20,
        clientY: 20,
        radiusX: 1,
        radiusY: 1,
        rotationAngle: 0,
        force: 1,
      });
      element.dispatchEvent(
        new TouchEvent("touchstart", {
          bubbles: true,
          cancelable: true,
          touches: [touch],
          targetTouches: [touch],
          changedTouches: [touch],
        }),
      );
    });
    await expect(copyItem(page)).toBeVisible();
    await rowA.dispatchEvent("touchend", { changedTouches: [] });
    await rowA.dispatchEvent("click");

    await expect(copyItem(page)).toBeEnabled();
    await copyItem(page).tap();

    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip.length).toBeGreaterThan(0);

    await expect(page).toHaveURL(new RegExp(`/agents/${threadB}$`));

    await context.close();
  });

  test("shared active thread appears in the sidebar with sandbox action", async ({
    page,
    browser,
    baseURL,
  }, testInfo) => {
    await loginAs(page, SAME_USER);
    const threadId = await createThreadWithSandbox(page);

    const bobContext = await browser.newContext({ baseURL });
    await bobContext.grantPermissions(["clipboard-read", "clipboard-write"], {
      origin: baseURL,
    });
    const bobPage = await bobContext.newPage();
    await loginAs(bobPage, OTHER_USER);
    await bobPage.goto(`/agents/${threadId}`);
    await expect(bobPage).toHaveURL(new RegExp(`/agents/${threadId}$`));

    const row = bobPage.locator(`a[href$="/agents/${threadId}"]`).first();
    await expect(row).toBeVisible();
    await row.press("Shift+F10");
    await expect(copyItem(bobPage)).toBeEnabled();

    const screenshotPath = testInfo.outputPath("shared-thread-sidebar-sandbox-menu.png");
    await bobPage.screenshot({ path: screenshotPath, fullPage: true });
    await testInfo.attach("shared-thread-sidebar-sandbox-menu", {
      path: screenshotPath,
      contentType: "image/png",
    });

    await bobContext.close();
  });
});
