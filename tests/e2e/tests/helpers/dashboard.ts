import { expect, type Page } from "@playwright/test";

// Shared fixtures for the specs that drive the REAL built ui/ app (served
// same-origin from the harness). Only the LLM/GitHub/Slack/token boundaries
// are faked.
export const SAME_USER = { login: "alice", email: "alice@example.com" };
export const OTHER_USER = { login: "bob", email: "bob@example.com" };

export async function loginAs(
  page: Page,
  user: { login: string; email: string },
) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
}

// The composer is a rich-text editor, not a <textarea>: it carries the prompt
// as `aria-placeholder` plus a visible overlay, so `getByPlaceholder` (which
// only matches the `placeholder` attribute) can't see it. Assert on both hooks
// so the visible prompt text stays covered.
export function composerFor(page: Page, placeholder: RegExp) {
  return {
    editor: page.getByTestId("composer-editor"),
    prompt: page.getByText(placeholder),
  };
}

// Typing goes through real key events rather than `fill()`: the editor builds
// its state from beforeinput/keydown, and `fill()`'s single bulk insert leaves
// it out of sync with the DOM.
export async function typeIntoComposer(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

export async function setRepoPrivate(page: Page, value: boolean) {
  const res = await page.request.post("/control/repo-private", {
    data: { private: value },
  });
  expect(res.ok()).toBeTruthy();
}

export async function setPullRequestHealth(
  page: Page,
  values: Record<string, unknown>,
) {
  const res = await page.request.post("/control/pull-request-health", {
    data: { number: 1, ...values },
  });
  expect(res.ok()).toBeTruthy();
}

// Hold the fake run open long enough to load its busy composer and queue a
// follow-up. It may finish while the UI observes the next server refresh.
export async function openRunningThreadViaSlackLink(page: Page) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page
    .locator("#text")
    .fill("<@U0BOT> E2E_BUSY_HOLD:8 please add a greet() helper and open a PR");
  await page.locator("#send").click();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

// Run the Slack flow so a thread + PR exist, then click the bot's real
// "Open in Web" link, landing on the actual dashboard app.
export async function openThreadViaSlackLink(
  page: Page,
  options: { repoPrivate?: boolean; message?: string } = {},
) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  if (options.repoPrivate) {
    await setRepoPrivate(page, true);
  }
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page
    .locator("#text")
    .fill(
      options.message ?? "<@U0BOT> please add a greet() helper and open a PR",
    );
  await page.locator("#send").click();
  await expect(
    page.locator(".msg.bot").filter({ hasText: "Add greet() helper" }),
  ).toBeVisible({ timeout: 60_000 });

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

// The SDK hydrates an idle thread's transcript from getState on load, which can
// briefly lag; a reload re-fetches it. Retry until the PR link renders.
export async function openMultiRepoPrThreadViaSlackLink(page: Page) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await page
    .locator("#text")
    .fill(
      "<@U0BOT> E2E_MULTI_PR open related pull requests in both repositories",
    );
  await page.locator("#send").click();
  await expect(
    page.locator(".msg.bot").filter({ hasText: "anotherorg/companion" }),
  ).toBeVisible({ timeout: 60_000 });

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

export function threadIdFromUrl(page: Page): string {
  const id = new URL(page.url()).pathname.split("/").pop() ?? "";
  expect(id).not.toBe("");
  return id;
}

export async function expectTranscriptVisible(page: Page) {
  await expect(async () => {
    await page.reload();
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible({ timeout: 8000 });
  }).toPass({ timeout: 60000 });
}

export async function waitForThreadIdle(page: Page, threadId: string) {
  await expect
    .poll(
      async () => {
        const res = await page.request.get(
          `/dashboard/api/threads/${threadId}?mark_viewed=false`,
        );
        if (!res.ok()) return "unknown";
        return ((await res.json()) as { status?: string }).status ?? "unknown";
      },
      { timeout: 30_000, intervals: [500] },
    )
    .not.toBe("running");
}

export async function waitForThreadNotBusy(page: Page, threadId: string) {
  await expect
    .poll(
      async () => {
        const res = await page.request.get(`/threads/${threadId}`);
        if (!res.ok()) return "unknown";
        return ((await res.json()) as { status?: string }).status ?? "unknown";
      },
      { timeout: 30_000, intervals: [500] },
    )
    .not.toBe("busy");
}

export async function threadState(
  page: Page,
  threadId: string,
): Promise<string> {
  const res = await page.request.get(
    `/dashboard/api/threads/${threadId}/state`,
  );
  if (!res.ok()) return "";
  return JSON.stringify(await res.json());
}

export async function waitForStateToContain(
  page: Page,
  threadId: string,
  text: string,
) {
  await expect
    .poll(() => threadState(page, threadId), {
      timeout: 60_000,
      intervals: [500],
    })
    .toContain(text);
}

export async function latestPrBody(page: Page): Promise<string> {
  const res = await page.request.get("/mock/github/data");
  expect(res.ok()).toBeTruthy();
  const prs = (await res.json()) as Array<{ body?: string }>;
  expect(prs.length).toBeGreaterThan(0);
  return prs[prs.length - 1]?.body ?? "";
}

export async function openThreadActionsMenu(page: Page) {
  const threadId = threadIdFromUrl(page);
  await page
    .locator(`[data-sidebar-frame] a[href="/agents/${threadId}"]`)
    .click({ button: "right" });
}
