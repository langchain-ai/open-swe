import { test, expect, type Page } from "@playwright/test";

// Drives the REAL built ui/ app (served same-origin from the harness) for the
// Slack → web handoff. Only the LLM/GitHub/Slack/token boundaries are faked.
const SAME_USER = { login: "alice", email: "alice@example.com" };
const OTHER_USER = { login: "bob", email: "bob@example.com" };

async function loginAs(page: Page, user: { login: string; email: string }) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
}

// The composer is a rich-text editor, not a <textarea>: it carries the prompt
// as `aria-placeholder` plus a visible overlay, so `getByPlaceholder` (which
// only matches the `placeholder` attribute) can't see it. Assert on both hooks
// so the visible prompt text stays covered.
function composerFor(page: Page, placeholder: RegExp) {
  return {
    editor: page.getByTestId("composer-editor"),
    prompt: page.getByText(placeholder),
  };
}

// Typing goes through real key events rather than `fill()`: the editor builds
// its state from beforeinput/keydown, and `fill()`'s single bulk insert leaves
// it out of sync with the DOM.
async function typeIntoComposer(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

async function setRepoPrivate(page: Page, value: boolean) {
  const res = await page.request.post("/control/repo-private", {
    data: { private: value },
  });
  expect(res.ok()).toBeTruthy();
}

// E2E_BUSY_HOLD:5 makes the fake LLM hold the run open for 5s. The window has to
// outlast the click through to the thread plus one reload — once the run
// finishes the retry loop below can never pass — so it keeps a wide margin.
async function openRunningThreadViaSlackLink(page: Page) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page
    .locator("#text")
    .fill("<@U0BOT> E2E_BUSY_HOLD:5 please add a greet() helper and open a PR");
  await page.locator("#send").click();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

// Run the Slack flow so a thread + PR exist, then click the bot's real
// "Open in Web" link, landing on the actual dashboard app.
async function openThreadViaSlackLink(
  page: Page,
  options: { repoPrivate?: boolean } = {},
) {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  if (options.repoPrivate) {
    await setRepoPrivate(page, true);
  }
  await expect(page.locator("#thread")).toContainText("No messages yet");
  await page
    .locator("#text")
    .fill("<@U0BOT> please add a greet() helper and open a PR");
  await page.locator("#send").click();
  await expect(
    page.locator(".msg.bot").filter({ hasText: "Add greet() helper" }),
  ).toBeVisible();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
}

// The SDK hydrates an idle thread's transcript from getState on load, which can
// briefly lag; a reload re-fetches it. Retry until the PR link renders.
async function expectTranscriptVisible(page: Page) {
  await expect(async () => {
    await page.reload();
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible({ timeout: 8000 });
  }).toPass({ timeout: 60000 });
}

async function waitForThreadIdle(page: Page, threadId: string) {
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

async function latestPrBody(page: Page): Promise<string> {
  const res = await page.request.get("/mock/github/data");
  expect(res.ok()).toBeTruthy();
  const prs = (await res.json()) as Array<{ body?: string }>;
  expect(prs.length).toBeGreaterThan(0);
  return prs[prs.length - 1]?.body ?? "";
}

async function openThreadActionsMenu(page: Page) {
  await page
    .getByRole("link", { name: /please add a greet/ })
    .first()
    .hover();
  const actionsButton = page
    .getByRole("button", { name: "Thread actions" })
    .first();
  await expect(actionsButton).toBeVisible();
  await actionsButton.click();
}

test.describe("Slack → web handoff (real dashboard UI)", () => {
  test("the SAME user continues the conversation in the web app", async ({
    page,
  }, testInfo) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);

    // The owner sees the composer (either the follow-up bar once the transcript
    // hydrates, or the empty-state bar before it — both mean they can type).
    const composer = composerFor(
      page,
      /Add a follow up|Send the first message/,
    );
    await expect(composer.editor).toBeVisible();
    await expect(composer.prompt).toBeVisible();
    // The context meter is an icon-only ring, so the numbers live in its
    // accessible name and in the popover it opens on hover — not in its text.
    const contextIndicator = page.getByTestId("context-window-indicator");
    await expect(contextIndicator).toBeVisible();
    await expect(contextIndicator).toHaveAccessibleName(/context|%|tokens/i);
    await contextIndicator.hover();
    await expect(page.getByText("Context window").first()).toBeVisible();
    const screenshotPath = testInfo.outputPath(
      "context-window-indicator-dashboard.png",
    );
    await page.screenshot({ path: screenshotPath, fullPage: true });
    await testInfo.attach("context-window-indicator-dashboard", {
      path: screenshotPath,
      contentType: "image/png",
    });

    // Continue from the web — a new agent reply streams into the same thread.
    await typeIntoComposer(page, "Looks good — can you also add a docstring?");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();

    // The transcript that started in Slack is here too (incl. the PR link).
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible();
  });

  // A cold load of a finished thread must hydrate from `getState()` alone. The
  // event stream is blocked so run replay can't stand in for that read: a
  // long-finished run has no replay left, which is what makes a broken hydrate
  // surface as a permanently empty transcript.
  test("a cold load renders a finished thread's transcript without run replay", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");
    await waitForThreadIdle(page, threadId);

    await page.route("**/stream/events", (route) => route.abort());
    await page.goto(`/agents/${threadId}`);
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      page.getByText("This thread has no messages yet."),
    ).toHaveCount(0);
  });

  test("streams after thread navigation and foreground recovery", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");
    await waitForThreadIdle(page, threadId);

    await page.getByRole("link", { name: "New Agent" }).click();
    await expect(page).toHaveURL(/\/agents\/?$/);
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/agents/${threadId}$`));
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible();

    const hydrated = page.waitForResponse((response) => {
      const path = new URL(response.url()).pathname;
      return (
        response.request().method() === "GET" &&
        path === `/dashboard/api/threads/${threadId}/state`
      );
    });
    await page.evaluate(() =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect((await hydrated).ok()).toBeTruthy();

    await typeIntoComposer(page, "Can you also add a docstring?");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Add greet() helper" }).first(),
    ).toBeVisible();
  });

  test("does not expose the originating Slack thread for public repos", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page);

    await openThreadActionsMenu(page);
    await expect(page.getByText("Open Slack thread")).toHaveCount(0);
    await expect.poll(() => latestPrBody(page)).not.toContain("Slack thread");
  });

  test("exposes the originating Slack thread for private repos", async ({
    page,
  }) => {
    await loginAs(page, SAME_USER);
    await openThreadViaSlackLink(page, { repoPrivate: true });

    await openThreadActionsMenu(page);
    const sourceItem = page.getByText("Open Slack thread");
    await expect(sourceItem).toBeVisible();
    const popupPromise = page.waitForEvent("popup");
    await sourceItem.click();
    const popup = await popupPromise;
    await expect(popup).toHaveURL(/\/mock\/slack/);
    await expect.poll(() => latestPrBody(page)).toContain("Slack thread");
  });

  test("keeps follow-ups visible while queued during a running agent", async ({
    page,
  }, testInfo) => {
    await loginAs(page, SAME_USER);
    await openRunningThreadViaSlackLink(page);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");

    const queuedText = "Please queue this follow-up while you finish the PR.";
    const busyComposer = composerFor(page, /Send a message to queue next/);
    await expect(async () => {
      await page.reload();
      await expect(busyComposer.prompt).toBeVisible({ timeout: 8000 });
    }).toPass({ timeout: 60000 });
    await typeIntoComposer(page, queuedText);

    const queuedMessage = page
      .getByTestId("queued-message")
      .filter({ hasText: queuedText });
    await expect(queuedMessage).toBeVisible();
    const screenshotPath = testInfo.outputPath("queued-messages-dashboard.png");
    await page.screenshot({ path: screenshotPath, fullPage: true });
    await testInfo.attach("queued-messages-dashboard", {
      path: screenshotPath,
      contentType: "image/png",
    });

    const serverRefresh = await page.waitForResponse((response) => {
      const path = new URL(response.url()).pathname;
      return (
        response.request().method() === "GET" &&
        path === `/dashboard/api/threads/${threadId}`
      );
    });
    expect(serverRefresh.ok()).toBeTruthy();
    await expect(serverRefresh.json()).resolves.toMatchObject({
      status: "running",
    });
    await expect(queuedMessage).toBeVisible();
  });

  test("stops a Slack-started run from the web app", async ({ page }) => {
    await loginAs(page, SAME_USER);
    await page.goto("/mock/slack");
    await page.locator("#reset").click();

    const send = await page.request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> E2E_BUSY_HOLD please add a greet() helper and open a PR",
      },
    });
    expect(send.ok()).toBeTruthy();
    const { thread_id: threadId } = (await send.json()) as {
      thread_id: string;
    };

    await page.goto(`/agents/${threadId}`);
    const stopButton = page.getByRole("button", { name: "Stop run" });
    await expect(stopButton).toBeVisible();

    const cancelResponsePromise = page.waitForResponse((response) => {
      const path = new URL(response.url()).pathname;
      return (
        response.request().method() === "POST" &&
        path === `/dashboard/api/threads/${threadId}/cancel`
      );
    });
    await stopButton.click();

    const cancelResponse = await cancelResponsePromise;
    expect(cancelResponse.ok()).toBeTruthy();
    await expect(cancelResponse.json()).resolves.toMatchObject({
      id: threadId,
      status: "interrupted",
    });
    await expect(
      page.getByRole("button", { name: "Send message" }),
    ).toBeVisible();
    await expect(stopButton).toHaveCount(0);
  });

  test("a DIFFERENT user can post, and their message is attributed", async ({
    page,
  }) => {
    await loginAs(page, OTHER_USER);
    await openThreadViaSlackLink(page);

    // The same thread + transcript is visible…
    await expectTranscriptVisible(page);

    // …and a non-owner now gets a composer too (owner-only restriction removed).
    const composer = composerFor(
      page,
      /Add a follow up|Send the first message/,
    );
    await expect(composer.editor).toBeVisible();
    await expect(composer.prompt).toBeVisible();

    // Posting starts a new run — the agent's follow-up reply streams in.
    await typeIntoComposer(page, "Can you also add a docstring?");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();

    // The non-owner's message is tagged server-side with their GitHub login, so
    // the owner can tell who sent it.
    await expect(
      page.getByText(new RegExp(`@${OTHER_USER.login}`)).first(),
    ).toBeVisible();
  });
});
